"""Six PAPER variants isolate price observation, entry confirmation and exit policy."""
import math
from collections import Counter,defaultdict
from a7.lab import Arm as PreviousArm
from a8.signals import ChainSignals,POLICY
from a6.tails import Tails

class Arm(PreviousArm):
    def __init__(self,name,chain,emit,entry=False,exit=False,source='AGGTRADE_LAST_EXECUTED'):
        super().__init__(name,chain,emit,entry,exit);self.price_source=source;self.price_ids={};self.feature_cache={}
    def features(self,s,now):
        # Reuse within the same decision timestamp only when both observed histories match.
        h=self.history.get(s,());f=self.flows.get(s,())
        key=(now,len(h),h[-1] if h else None,len(f),f[-1] if f else None)
        cached=self.feature_cache.get(s)
        if cached is None or cached[0]!=key:
            value=super().features(s,now);cached=(key,value);self.feature_cache[s]=cached
        return dict(cached[1]) if cached[1] is not None else None
    def ingest(self,row,now):
        if self.price_source=='MINITICKER':return super().ingest(row,now)
        if row['kind']=='wide':
            data=row.get('data');items=data if isinstance(data,list) else [data]
            row=dict(row,data=[r for r in items if isinstance(r,dict) and r.get('e')!='24hrMiniTicker'])
        before=self.counts['flow_buckets'];super().ingest(row,now)
        if self.price_source!='AGGTRADE_LAST_EXECUTED' or row['kind']!='flow' or self.counts['flow_buckets']==before:return
        p=row.get('price');ident=row.get('price_trade_id');s=row['symbol'];t=row['event_ms'];generation=row.get('generation',0)
        if row.get('price_source')!='AGGTRADE_LAST_EXECUTED' or isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or p<=0 or not isinstance(ident,int) or ident<=0:
            self.counts['missing_trade_price']+=1;return
        old=self.price_ids.get(s)
        if old and generation==old[0] and ident<=old[1]:self.counts['duplicate_trade_price']+=1;return
        h=self.history[s]
        if h and t<=h[-1][0]:return
        self.price_ids[s]=(generation,ident)
        if h and t//2000==h[-1][0]//2000:h[-1]=(t,p)
        else:h.append((t,p))
        self.counts['trade_prices']+=1
    def coverage_reason(self,s,now):
        sources=self.chain.registry[s]['sources']
        if not sources:return 'POOL_UNAVAILABLE'
        healthy=False
        for p in sources:
            status=self.chain.statuses.get(p['chain'],{})
            if status.get('state')=='CONNECTED' and 0<=now-status.get('last',0)<=20000 and now-status['since']>=POLICY['min_chain_warmup_ms']:healthy=True
        return None if healthy else 'CHAIN_UNAVAILABLE'
    def reason(self,s,now):
        # Missing observation is distinct from a valid observation without a sell signal.
        if self.new_entry:
            reason=self.coverage_reason(s,now)
            if reason:return reason,self.features(s,now)
        reason,f=super().reason(s,now)
        if reason=='PRICE_WARMUP':return self.price_reason(s,now,f),f
        return reason,f
    def price_reason(self,s,now,f=None):
        h=self.history.get(s,())
        if not h:return 'PRICE_NOT_OBSERVED'
        if now-h[-1][0]>10000:return 'PRICE_STALE'
        f=f or self.features(s,now)
        if f is None or f['r30'] is None or f['r120'] is None:return 'PRICE_REFERENCE_GAP'
        if f['vol_bps'] is None:return 'PRICE_SAMPLE_INSUFFICIENT'
        if f['max_price_gap_ms']>15000:return 'PRICE_OBSERVATION_GAP'
        return None
    def on_book(self,s,now):
        if self.price_source=='BOOK_MID':
            b=self.books[s];t=b['event_ms'];h=self.history[s];p=(b['bids'][0][0]+b['asks'][0][0])/2
            if not h or t>h[-1][0]:
                if h and t//2000==h[-1][0]//2000:h[-1]=(t,p)
                else:h.append((t,p))
                self.counts['book_mid_prices']+=1
        if self.deadline and now>=self.deadline and s in self.pending:
            self.event('ENTRY_CANCELLED',now,symbol=s,reason='ENTRY_WINDOW_ENDED');del self.pending[s]
        super().on_book(s,now)
    def gap(self,channel,at,reason):
        self.price_ids.clear();self.feature_cache.clear()
        if (self.price_source=='BOOK_MID' and channel=='depth') or (self.price_source=='AGGTRADE_LAST_EXECUTED' and channel=='flow'):self.history.clear()
        super().gap(channel,at,reason)
    def view(self,now,selected):
        v=super().view(now,selected);existing={r['symbol']:r for r in v['leaders']};leaders=[]
        for s in selected:
            reason,f=self.reason(s,now)
            leaders.append(dict(existing.get(s,dict(symbol=s,chart=[],r120=None)),gate=reason or 'CANDIDATE',price_quality=self.price_reason(s,now,f),chain_quality=self.coverage_reason(s,now)))
        v.update(leaders=leaders,price_source=self.price_source);return v

class Lab:
    def __init__(self,registry,emit):
        self.symbols=[c['symbol'] for c in registry];self.chain=ChainSignals(registry)
        self.arms=[Arm('CONTROLE',self.chain,emit,source='MINITICKER'),Arm('PRECO',self.chain,emit),Arm('LIVRO',self.chain,emit,source='BOOK_MID'),Arm('ENTRADA',self.chain,emit,True,source='BOOK_MID'),Arm('SAIDA',self.chain,emit,False,True,source='BOOK_MID'),Arm('COMBINADO',self.chain,emit,True,True,source='BOOK_MID')]
        self.price_arm=self.arms[2];self.entry_arm=self.arms[3];self.trade_arm=self.arms[1]
        self.inputs=Counter();self.last_ms=0;self.phase='PREPARING';self.started=None;self.deadline=None;self.last_tick=None
        self.quality=defaultdict(Counter);self.price_seen=set();self.coverage=defaultdict(Counter)
    def ingest(self,row,now):
        if now<self.last_ms:raise ValueError('REPLAY_TIME_REVERSED')
        self.last_ms=now;self.inputs[row['kind']]+=1
        if row['kind']=='session':
            self.started=row['started_ms'];self.deadline=row['deadline_ms'];self.phase='CAPTURING';self.last_tick=now
            for a in self.arms:a.start=self.started;a.deadline=self.deadline
        elif row['kind']=='end_entries':
            self.phase='FOLLOWUP'
            for a in self.arms:
                for s in list(a.pending):a.event('ENTRY_CANCELLED',now,symbol=s,reason='ENTRY_WINDOW_ENDED')
                a.pending.clear()
        elif row['kind'] in ('chain_trade','chain_status'):self.chain.ingest(row,now)
        elif row['kind']=='tick':
            for a in self.arms:a.tick(now,self.symbols)
            dt=min(2000,max(0,now-(self.last_tick or now)));self.last_tick=now
            for s in self.symbols:
                a=self.price_arm;price=a.price_reason(s,now);chain=self.entry_arm.coverage_reason(s,now)
                try:a.ready(s,now);book=True
                except ValueError:book=False
                rowq=self.quality[s];rowq['ticks']+=1;rowq[price or 'PRICE_READY']+=1
                if book:rowq['BOOK_READY']+=1
                if chain is None:rowq['CHAIN_READY']+=1
                if not price and book and not chain:rowq['ALL_DATA_READY']+=1
                if self.phase=='CAPTURING':
                    self.coverage[s]['observed_tick_ms']+=dt
                    if not price and book and not chain:self.coverage[s]['all_data_ready_sampled_ms']+=dt
        elif row['kind']=='finish':
            for a in self.arms:a.finish(now)
            self.phase='FINISHED'
        elif row['kind']=='gap':
            for a in self.arms:a.gap('all',now,row['reason'])
            for chain in ('solana','bsc'):self.chain.ingest(dict(kind='chain_status',chain=chain,state='GAP',reason=row['reason']),now)
        elif row['kind'] not in ('sampling','chain_evidence'):
            if row['kind']=='metadata':row=dict(row,data=dict(row['data'],symbols=[r for r in row['data'].get('symbols',[]) if r['symbol'] in self.symbols]))
            for a in self.arms:a.ingest(row,now)
            if row['kind']=='depth':
                symbol=row.get('data',{}).get('s')
                if symbol in self.price_arm.history and self.price_arm.history[symbol]:self.price_seen.add(symbol)
    def view(self,now):
        return dict(at_ms=now,orders=False,phase=self.phase,inputs=dict(self.inputs),policy=POLICY,
            chains=self.chain.statuses,chain_counts=dict(self.chain.counts),price_seen=sorted(self.price_seen),
            quality={s:dict(v) for s,v in self.quality.items()},coverage={s:dict(v) for s,v in self.coverage.items()},
            arms={a.name:a.view(now,self.symbols) for a in self.arms})
