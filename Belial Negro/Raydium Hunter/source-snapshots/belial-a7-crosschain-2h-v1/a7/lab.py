"""Four causal PAPER arms. Identical market stream, fee assumptions and risk limits."""
from collections import defaultdict,deque,Counter,OrderedDict
from decimal import Decimal,ROUND_DOWN
import math
from a6.tails import Tails,CONFIG

POLICY=dict(risk_usdt=1.,max_notional=100.,min_wallet_sells=2,wallet_window_ms=60000,
    min_wallet_sells_value_usdt=500.,max_chain_age_ms=15000,max_signal_age_ms=15000,
    min_chain_warmup_ms=10000,short_confirmation_bps=-3.,max_drop120_bps=-250.,
    failure_min_hold_ms=30000,failure_persist_ms=5000,failure_loss_bps=10.,failure_sell_fraction=.45,
    fee_bps=5.,stress_bps=3.,orders=False)

class ChainSignals:
    def __init__(self,registry):
        self.registry={c['symbol']:c for c in registry};self.history=defaultdict(lambda:deque(maxlen=256))
        self.statuses={};self.seen=OrderedDict();self.counts=Counter()
    def ingest(self,event,now):
        chain=event.get('chain');kind=event['kind']
        if kind=='chain_status':
            state=event['state'];old=self.statuses.get(chain,{})
            if state=='CONNECTED':self.statuses[chain]=dict(state=state,since=now,last=now)
            elif state=='HEARTBEAT':
                if old.get('state')=='CONNECTED':old['last']=now
                elif old.get('state')=='GAP':self.statuses[chain]=dict(state='CONNECTED',since=now,last=now)
            elif state in ('GAP','FAILED'):
                self.statuses[chain]=dict(state=state,since=now,last=now,reason=event.get('reason'))
                for key in list(self.history):
                    if key[0]==chain:del self.history[key]
            self.counts[chain+'_'+state]+=1;return
        if kind!='chain_trade':return
        self.counts[chain+'_attributions']+=1
        c=self.registry.get(event['symbol'])
        if not c:return
        if not any(p['chain']==chain and p['pool']==event['pool'] and p['mint']==event['mint'] for p in c['sources']):return
        key=(chain,event['tx'],event['wallet'],event['mint'])
        if key in self.seen:self.counts['duplicates']+=1;return
        self.seen[key]=True
        while len(self.seen)>16384:self.seen.popitem(last=False)
        available=event['available_ms'];chain_time=event.get('chain_time_ms')
        if available>now or now-available>2000 or chain_time is None or not 0<=now-chain_time<=POLICY['max_chain_age_ms']:
            self.counts['late_or_unknown_timestamp']+=1;return
        if event.get('qualified_sell') is not True:self.counts['not_verified_net_sell']+=1;return
        quantity=event.get('quantity',0)
        if not isinstance(quantity,(int,float)) or not math.isfinite(quantity) or quantity<=0:return
        if len(self.history)>=8192 and (chain,event['symbol'],event['wallet']) not in self.history:
            del self.history[next(iter(self.history))];self.counts['wallet_history_evicted']+=1
        self.history[(chain,event['symbol'],event['wallet'])].append(dict(event,decision_available_ms=now))
        self.counts[chain+'_qualified_sells']+=1
    def signal(self,s,now,contract_price):
        c=self.registry[s];matches=[]
        for (chain,symbol,wallet),events in self.history.items():
            if symbol!=s:continue
            status=self.statuses.get(chain,{})
            if status.get('state')!='CONNECTED' or now-status['since']<POLICY['min_chain_warmup_ms'] or now-status['last']>20000:continue
            recent=[r for r in events if 0<=now-r['decision_available_ms']<=POLICY['wallet_window_ms']]
            if len(recent)<POLICY['min_wallet_sells'] or now-recent[-1]['decision_available_ms']>POLICY['max_signal_age_ms']:continue
            value=sum(r['quantity'] for r in recent)*contract_price/c['multiplier']
            if value<POLICY['min_wallet_sells_value_usdt']:continue
            matches.append(dict(chain=chain,wallet=wallet,count=len(recent),estimated_value_usdt=value,
                last_available_ms=recent[-1]['decision_available_ms'],transactions=[r['tx'] for r in recent],
                value_basis='CURRENT_BINANCE_REFERENCE_NOT_ONCHAIN_EXECUTION_PRICE'))
        return sorted(matches,key=lambda r:(-r['last_available_ms'],r['wallet']))

class Arm(Tails):
    def __init__(self,name,chain,emit,entry=False,exit=False):
        super().__init__(lambda e:emit(dict(e,arm=name)),dict(max_deep=20))
        self.name=name;self.chain=chain;self.new_entry=entry;self.new_exit=exit;self.failure_since={}
    def quote(self,s,now,qty=None):
        if qty is None:
            f=self.features(s,now);vol=f.get('vol_bps') if f else None
            if vol is None:raise ValueError('RISK_VOL_WARMUP')
            stop=max(75.,min(300.,3*vol));notional=min(POLICY['max_notional'],POLICY['risk_usdt']/((stop+2*self.cfg['fee_bps']+self.cfg['stress_bps'])/10000))
            b=self.ready(s,now);mid=(b['bids'][0][0]+b['asks'][0][0])/2
            step=Decimal(self.catalog[s]['step']);qty=float((Decimal(str(notional/mid))/step).to_integral_value(rounding=ROUND_DOWN)*step)
        return super().quote(s,now,qty)
    def short_flow(self,s,now):
        rows=[r for r in self.flows.get(s,()) if 0<=now-r[0]<=10000]
        if not rows or now-rows[-1][0]>2500 or rows[-1][0]-rows[0][0]<7000:return None
        buy=sum(r[1] for r in rows);sell=sum(r[2] for r in rows)
        return sell/(buy+sell) if buy+sell else None
    def reason(self,s,now):
        reason,f=super().reason(s,now)
        if reason or not self.new_entry:return reason,f
        signals=self.chain.signal(s,now,f['price']);f=dict(f,onchain=signals)
        if not signals:return 'NO_FRESH_RECURRENT_NET_SELL',f
        if f['r120']<POLICY['max_drop120_bps']:return 'EXTENDED_DROP',f
        h=self.history[s];ref=next(((t,p) for t,p in reversed(h) if t<=now-10000),None)
        if not ref or now-10000-ref[0]>3000:return 'SHORT_CONFIRMATION_WARMUP',f
        r10=(f['price']/ref[1]-1)*10000;flow=self.short_flow(s,now);f.update(r10=r10,short_sell_fraction=flow)
        if r10>POLICY['short_confirmation_bps'] or flow is None or flow<.60:return 'CONTINUATION_NOT_CONFIRMED',f
        return None,f
    def tick(self,now,selected):
        # Same causal ranking for all arms prevents arbitrary alphabetical slot allocation.
        def score(s):
            f=self.features(s,now)
            return ((f or {}).get('sell_fraction',0),-((f or {}).get('r120') or 0),s)
        super().tick(now,sorted(selected,key=score,reverse=True))
    def on_book(self,s,now):
        super().on_book(s,now)
        p=self.positions.get(s)
        if not p:self.failure_since.pop(s,None);return
        if not self.new_exit or p['exit_request'] is not None:return
        try:q=self.quote(s,now,p['qty'])['exit']
        except ValueError:return
        gross=(p['entry']['quote']-q['quote'])/p['entry']['quote']*10000
        flow=self.short_flow(s,now)
        failed=(now-p['opened_ms']>=POLICY['failure_min_hold_ms'] and gross<=-POLICY['failure_loss_bps']
            and flow is not None and flow<=POLICY['failure_sell_fraction'])
        if not failed:self.failure_since.pop(s,None);return
        since=self.failure_since.setdefault(s,now)
        if now-since>=POLICY['failure_persist_ms']:
            p['exit_request']=now;p['exit_reason']='THESIS_FAILED_BUY_ABSORPTION'
            self.event('EXIT_REQUEST',now,symbol=s,reason=p['exit_reason'],sell_fraction_10s=flow)
    def gap(self,channel,at,reason):
        self.failure_since.clear();super().gap(channel,at,reason)

class Lab:
    def __init__(self,registry,emit):
        self.symbols=[c['symbol'] for c in registry];self.chain=ChainSignals(registry)
        self.arms=[Arm('CONTROLE',self.chain,emit),Arm('ENTRADA',self.chain,emit,True),
            Arm('SAIDA',self.chain,emit,False,True),Arm('COMBINADO',self.chain,emit,True,True)]
        self.inputs=Counter();self.last_ms=0
    def ingest(self,row,now):
        if now<self.last_ms:raise ValueError('REPLAY_TIME_REVERSED')
        self.last_ms=now;self.inputs[row['kind']]+=1
        if row['kind']=='session':
            for a in self.arms:a.start=row.get('started_ms');a.deadline=row.get('deadline_ms')
        elif row['kind'] in ('chain_trade','chain_status'):self.chain.ingest(row,now)
        elif row['kind']=='tick':
            for a in self.arms:a.tick(now,self.symbols)
        elif row['kind']=='finish':
            for a in self.arms:a.finish(now)
        elif row['kind']=='gap':
            for a in self.arms:a.gap('all',now,row['reason'])
            for chain in ('solana','bsc'):self.chain.ingest(dict(kind='chain_status',chain=chain,state='GAP',reason=row['reason']),now)
        elif row['kind'] not in ('sampling','chain_evidence'):
            if row['kind']=='metadata':
                row=dict(row,data=dict(row['data'],symbols=[r for r in row['data'].get('symbols',[]) if r['symbol'] in self.symbols]))
            for a in self.arms:a.ingest(row,now)
    def view(self,now):
        return dict(at_ms=now,orders=False,inputs=dict(self.inputs),policy=POLICY,
            chains=self.chain.statuses,chain_counts=dict(self.chain.counts),
            arms={a.name:a.view(now,self.symbols) for a in self.arms})
