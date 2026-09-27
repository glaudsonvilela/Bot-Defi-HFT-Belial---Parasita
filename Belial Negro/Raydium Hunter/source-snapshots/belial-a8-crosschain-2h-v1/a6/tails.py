"""Causal continuation detector and independent Binance PAPER portfolio.
Spot discovery never enters this engine. No cross-venue symbol heuristics.
"""
from collections import Counter, defaultdict, deque
from decimal import Decimal, ROUND_DOWN
import math

CONFIG = dict(max_deep=64, max_catalog=4096, notional=100., capital=1000., max_positions=8,
              fee_bps=5., fee_basis='ASSUMED_SCENARIO_NOT_ACCOUNT_FEE', latency_ms=300,
              expiry_ms=5000, book_age_ms=2000, max_cost_bps=30., stress_bps=3.,
              return30_bps=-12., return120_bps=-35., sell_fraction=.60,
              min_flow_notional=5000., min_trades=20, max_hold_ms=3600000,
              cooldown_ms=600000, orders=False)

def num(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):return None

def positive(v):
    n=num(v)
    return n if n is not None and n>0 else None

def contracts(document):
    result={}
    for r in document.get('symbols',[]):
        if r.get('status')!='TRADING' or r.get('contractType')!='PERPETUAL' or r.get('quoteAsset')!='USDT' or r.get('marginAsset')!='USDT':continue
        s=r.get('symbol','')
        if not s.isascii() or not s.isalnum() or len(s)>40:continue
        filters={f.get('filterType'):f for f in r.get('filters',[])}
        lot=filters.get('MARKET_LOT_SIZE') or filters.get('LOT_SIZE',{})
        step=positive(lot.get('stepSize'));mn=positive(lot.get('minQty'));mx=positive(lot.get('maxQty'))
        nf=filters.get('MIN_NOTIONAL') or filters.get('NOTIONAL',{})
        minimum=positive(nf.get('notional',nf.get('minNotional')))
        if not all((step,mn,mx,minimum)):continue
        result[s]=dict(step=str(lot['stepSize']),min_qty=mn,max_qty=mx,min_notional=minimum,base=r.get('baseAsset'),quote='USDT')
    return dict(list(result.items())[:CONFIG['max_catalog']])

def fill(levels,qty):
    remain=qty;value=0.;used=[]
    for px,amount in levels:
        q=min(remain,amount);remain-=q;value+=q*px;used.append([px,q])
        if remain<=max(1e-10,qty*1e-10):return dict(vwap=value/qty,quote=value,qty=qty,levels=used)
    raise ValueError('INSUFFICIENT_TOP20_DEPTH')

class Tails:
    def __init__(self,emit=lambda *_:None,config=None):
        self.cfg=dict(CONFIG,**(config or {}));self.emit=emit;self.catalog={};self.history=defaultdict(lambda:deque(maxlen=1201));self.flows=defaultdict(lambda:deque(maxlen=120));self.books={};self.funding={};self.pending={};self.positions={};self.closed=deque(maxlen=200);self.counts=Counter();self.last_signal={};self.rejections=Counter();self.last_diagnostic={};self.realized=0.;self.loss_epoch=0;self.channel_state={};self.metadata_ms=0;self.start=None;self.deadline=None
    def event(self,kind,at,**kw):self.emit(dict(kind=kind,at_ms=at,paper_only=True,orders=False,**kw))
    def gap(self,channel,at,reason):
        self.counts['gaps']+=1
        if channel in ('wide','all'):self.history.clear()
        if channel in ('flow','all'):self.flows.clear()
        if channel in ('depth','all'):self.books.clear()
        for s,p in list(self.pending.items()):self.event('ENTRY_CANCELLED',at,symbol=s,reason=reason);del self.pending[s]
        for p in self.positions.values():p['path_censored']=True
        self.event('DATA_GAP',at,channel=channel,reason=reason)
    def ingest(self,row,now):
        # Consumer time never disguises old producer receipts.
        received=int(row.get('received_ms',0));kind=row.get('kind')
        loss=int(row.get('loss_epoch',0))
        if loss!=self.loss_epoch:self.gap('all',now,'NATIVE_QUEUE_OVERFLOW');self.loss_epoch=loss
        if now-received>2000 or received>now+500:self.counts['stale_envelopes']+=1;return
        if kind=='transport':
            ch=row.get('channel');self.channel_state[ch]=row.get('state')
            if row.get('state')!='CONNECTED':self.gap(ch,now,row.get('state','UNKNOWN'))
        elif kind=='metadata':
            self.catalog=contracts(row['data']);self.metadata_ms=now;self.counts['metadata_refresh']+=1
        elif kind=='metadata_error':self.counts['metadata_errors']+=1
        elif kind=='wide':
            data=row.get('data');items=data if isinstance(data,list) else [data]
            for r in items:
                if not isinstance(r,dict) or r.get('s') not in self.catalog or r.get('st')==2:continue
                s=r['s'];t=int(r.get('E',0))
                if t>now+500 or now-t>10000:continue
                if r.get('e')=='markPriceUpdate':
                    self.funding[s]=dict(next_ms=int(r.get('T',0)),rate=num(r.get('r')),at_ms=now)
                elif r.get('e')=='24hrMiniTicker':
                    price=positive(r.get('c'));hist=self.history[s]
                    if not price or (hist and t<=hist[-1][0]):continue
                    # Two-second buckets bound RAM; do not insert cached stale samples.
                    if hist and t//2000==hist[-1][0]//2000:hist[-1]=(t,price)
                    else:hist.append((t,price))
                    self.counts['wide_prices']+=1
        elif kind=='flow':
            s=row.get('symbol');t=int(row.get('event_ms',0));v=self.flows[s] if s in self.catalog else None
            if v is None or t>now+500 or now-t>2500:return
            if v and t<=v[-1][0]:return
            buy=num(row.get('buy'));sell=num(row.get('sell'));n=int(row.get('trades',0))
            if buy is None or sell is None or min(buy,sell)<0 or n<1:return
            v.append((t,buy,sell,n));self.counts['flow_buckets']+=1
        elif kind=='depth':
            r=row.get('data',{});s=r.get('s');t=int(r.get('E',0));seq=int(r.get('u',0));generation=int(row.get('generation',0))
            if s not in self.catalog or r.get('st')==2 or now-t>self.cfg['book_age_ms'] or t>now+500:return
            old=self.books.get(s)
            if old and generation==old['generation'] and seq<=old['sequence']:return
            try:
                bids=self.levels(r.get('b'),True);asks=self.levels(r.get('a'),False)
                if bids[0][0]>=asks[0][0]:raise ValueError('CROSSED_BOOK')
            except (ValueError,TypeError,IndexError):self.books.pop(s,None);self.counts['invalid_books']+=1;return
            self.books[s]=dict(bids=bids,asks=asks,event_ms=t,received_ms=received,generation=generation,sequence=seq)
            self.counts['books']+=1;self.on_book(s,now)
    @staticmethod
    def levels(rows,reverse):
        if not isinstance(rows,list) or not rows or len(rows)>20:raise ValueError('TOP20_SHAPE')
        result=[]
        for row in rows:
            if not isinstance(row,list) or len(row)<2:raise ValueError('LEVEL_SHAPE')
            p=positive(row[0]);q=positive(row[1])
            if not p or not q:raise ValueError('INVALID_LEVEL')
            result.append((p,q))
        if len({r[0] for r in result})!=len(result):raise ValueError('DUPLICATE_PRICE')
        return sorted(result,reverse=reverse)
    def features(self,s,now):
        h=self.history.get(s,());f=self.flows.get(s,())
        if not h or now-h[-1][0]>10000:return None
        result={};px=h[-1][1]
        for seconds in (30,120,600):
            target=now-seconds*1000;ref=next(((t,p) for t,p in reversed(h) if t<=target),None)
            if ref is None or target-ref[0]>10000:result['r'+str(seconds)]=None
            else:result['r'+str(seconds)]=(px/ref[1]-1)*10000
        recent=[r for r in h if now-r[0]<=60000]
        continuity=[r for r in h if now-r[0]<=130000]
        result['max_price_gap_ms']=max([b[0]-a[0] for a,b in zip(continuity,continuity[1:])] or [0])
        diffs=[math.log(b[1]/a[1])*10000 for a,b in zip(recent,recent[1:])]
        result['vol_bps']=math.sqrt(sum(v*v for v in diffs)) if len(diffs)>=10 else None
        flow=[r for r in f if 0<=now-r[0]<=60000];buy=sum(r[1] for r in flow);sell=sum(r[2] for r in flow)
        result.update(price=px,flow=buy+sell,sell_fraction=sell/(buy+sell) if buy+sell else 0.,trades=sum(r[3] for r in flow),flow_span_ms=(flow[-1][0]-flow[0][0]) if flow else 0)
        return result
    def ready(self,s,now):
        b=self.books.get(s)
        if not b or now-b['received_ms']>self.cfg['book_age_ms'] or now-b['event_ms']>self.cfg['book_age_ms']:raise ValueError('STALE_OR_MISSING_BOOK')
        if now-self.metadata_ms>1200000 or s not in self.catalog:raise ValueError('METADATA_STALE')
        return b
    def quote(self,s,now,qty=None):
        b=self.ready(s,now);m=self.catalog[s]
        if qty is None:
            mid=(b['bids'][0][0]+b['asks'][0][0])/2
            step=Decimal(m['step']);qty=float((Decimal(str(self.cfg['notional']/mid))/step).to_integral_value(rounding=ROUND_DOWN)*step)
        if not m['min_qty']<=qty<=m['max_qty']:raise ValueError('LOT_FILTER')
        entry=fill(b['bids'],qty);exit=fill(b['asks'],qty)
        if min(entry['quote'],exit['quote'])<m['min_notional']:raise ValueError('MIN_NOTIONAL')
        cost=((exit['quote']-entry['quote'])+(entry['quote']+exit['quote'])*self.cfg['fee_bps']/10000)/entry['quote']*10000+self.cfg['stress_bps']
        return dict(qty=qty,entry=entry,exit=exit,cost_bps=cost,book=b)
    def reason(self,s,now):
        f=self.features(s,now)
        if not f or f['r30'] is None or f['r120'] is None or f['vol_bps'] is None:return 'PRICE_WARMUP',f
        if f['max_price_gap_ms']>15000:return 'PRICE_OBSERVATION_GAP',f
        if f['r30']>self.cfg['return30_bps'] or f['r120']>self.cfg['return120_bps']:return 'NO_PERSISTENT_DROP',f
        if f['r600'] is not None and f['r600']>0:return 'LONGER_TREND_OPPOSES',f
        if f['flow_span_ms']<45000:return 'FLOW_WARMUP',f
        if f['flow']<self.cfg['min_flow_notional'] or f['trades']<self.cfg['min_trades'] or f['sell_fraction']<self.cfg['sell_fraction']:return 'SELL_FLOW_NOT_CONFIRMED',f
        try:q=self.quote(s,now)
        except ValueError as e:return str(e),f
        if q['cost_bps']>self.cfg['max_cost_bps']:return 'COST_GATE',dict(f,quote=q)
        return None,dict(f,quote=q)
    def selected(self,now):
        # Pin open positions; preserve warmed candidates, reserve rotating exploration slots.
        ranked=[]
        for s,h in self.history.items():
            if s not in self.catalog:continue
            f=self.features(s,now)
            if f:ranked.append((-(f['r120'] if f['r120'] is not None else 0),s))
        ranked.sort(reverse=True);pinned=set(self.positions)|set(self.pending)
        hot=[s for _,s in ranked[:self.cfg['max_deep']-8]]
        rest=sorted(s for _,s in ranked if s not in hot and s not in pinned)
        start=((now//60000)*8)%max(1,len(rest));explore=(rest+rest)[start:start+8]
        return list(dict.fromkeys(sorted(pinned)+hot+explore))[:self.cfg['max_deep']]
    def tick(self,now,selected):
        for s,p in list(self.pending.items()):
            if now>p['expires_ms']:
                self.event('ENTRY_EXPIRED',now,symbol=s);self.counts['expired']+=1;del self.pending[s]
        for s in selected:
            if s in self.positions or s in self.pending:continue
            if self.start is None or now<self.start or (self.deadline and now>=self.deadline):continue
            if now-self.last_signal.get(s,0)<self.cfg['cooldown_ms']:continue
            reason,f=self.reason(s,now)
            if reason:
                self.rejections[reason]+=1
                if f and f.get('r120') is not None and f['r120']<=self.cfg['return120_bps'] and now-self.last_diagnostic.get(s,0)>30000:
                    self.event('CANDIDATE_REJECTED',now,symbol=s,reason=reason,features=f);self.last_diagnostic[s]=now
                continue
            if len(self.positions)+len(self.pending)>=self.cfg['max_positions']:self.rejections['PORTFOLIO_CAP']+=1;continue
            reserved=sum(p['notional'] for p in self.positions.values())+len(self.pending)*self.cfg['notional']
            if reserved+self.cfg['notional']*1.02>self.cfg['capital']+self.realized:self.rejections['CAPITAL']+=1;continue
            q=f['quote'];stop=max(75.,min(300.,3*f['vol_bps']))
            p=dict(symbol=s,qty=q['qty'],requested_ms=now,eligible_ms=now+self.cfg['latency_ms'],expires_ms=now+self.cfg['expiry_ms'],generation=q['book']['generation'],stop_bps=stop,activation_bps=max(100.,stop),giveback_bps=max(50.,stop*.5),max_hold_ms=self.cfg['max_hold_ms'])
            self.pending[s]=p;self.last_signal[s]=now;self.counts['signals']+=1;self.event('SIGNAL',now,features=f,**p)
        # Bounded caches; retain only active subscriptions and current catalogue.
        selected=set(selected)|set(self.positions)|set(self.pending)
        for table in (self.books,self.flows):
            for s in list(table):
                if s not in selected:table.pop(s,None)
        for table in (self.history,self.funding,self.last_signal,self.last_diagnostic):
            for s in list(table):
                if s not in self.catalog and s not in self.positions:table.pop(s,None)
    def on_book(self,s,now):
        b=self.books[s];pending=self.pending.get(s)
        if pending:
            if b['generation']!=pending['generation']:
                self.event('ENTRY_CANCELLED',now,symbol=s,reason='GENERATION_CHANGED');del self.pending[s]
            elif now<=pending['expires_ms'] and b['event_ms']>=pending['eligible_ms'] and b['received_ms']>=pending['eligible_ms']:
                reason,_=self.reason(s,now)
                if reason:self.event('ENTRY_CANCELLED',now,symbol=s,reason=reason);del self.pending[s]
                else:
                    try:q=self.quote(s,now,pending['qty'])
                    except ValueError:return
                    if q['cost_bps']>self.cfg['max_cost_bps']:return
                    committed=sum(p['notional']+p['entry_fee'] for p in self.positions.values())+sum(self.cfg['notional']*1.02 for key in self.pending if key!=s)
                    if committed+q['entry']['quote']*(1+self.cfg['fee_bps']/10000)>self.cfg['capital']+self.realized:
                        self.event('ENTRY_CANCELLED',now,symbol=s,reason='CAPITAL_AT_FILL');del self.pending[s];return
                    p=dict(pending,entry=q['entry'],opened_ms=now,notional=q['entry']['quote'],entry_fee=q['entry']['quote']*self.cfg['fee_bps']/10000,mfe_bps=0.,mae_bps=0.,path_censored=False,next_funding_ms=(self.funding.get(s) or {}).get('next_ms'),exit_request=None)
                    self.positions[s]=p;del self.pending[s];self.counts['entries']+=1;self.event('ENTRY_FILLED',now,position=p,book=b)
        p=self.positions.get(s)
        if not p:return
        try:q=fill(b['asks'],p['qty'])
        except ValueError:return
        gross=(p['entry']['quote']-q['quote'])/p['entry']['quote']*10000
        p['mfe_bps']=max(p['mfe_bps'],gross);p['mae_bps']=max(p['mae_bps'],-gross)
        p['close_now_net_ex_funding']=p['entry']['quote']-q['quote']-p['entry_fee']-q['quote']*self.cfg['fee_bps']/10000
        if p['exit_request'] is not None:
            if b['event_ms']>=p['exit_request']+self.cfg['latency_ms'] and b['received_ms']>=p['exit_request']+self.cfg['latency_ms']:
                p.update(closed_ms=now,exit=q,net_ex_funding=p['close_now_net_ex_funding'],all_in_net=None,funding_status='NOT_RECONCILED',external_costs_status='UNKNOWN')
                self.realized+=p['net_ex_funding'];self.closed.append(dict(p));del self.positions[s];self.counts['closed']+=1;self.event('EXIT_FILLED',now,position=p,book=b)
            return
        reason=None
        if gross<=-p['stop_bps']:reason='STOP'
        elif p['mfe_bps']>=p['activation_bps'] and p['mfe_bps']-gross>=p['giveback_bps']:reason='TRAIL'
        elif now-p['opened_ms']>=p['max_hold_ms']:reason='MAX_HOLD'
        if reason:p['exit_request']=now;p['exit_reason']=reason;self.event('EXIT_REQUEST',now,symbol=s,reason=reason)
    def finish(self,now):
        for s in list(self.pending):self.event('ENTRY_CANCELLED',now,symbol=s,reason='SESSION_END')
        self.pending.clear()
        for p in self.positions.values():p['right_censored']=True;self.event('OPEN_CENSORED',now,position=p)
    def view(self,now,selected):
        leaders=[]
        for s in selected:
            f=self.features(s,now);reason,_=self.reason(s,now)
            if f:leaders.append(dict(symbol=s,**f,gate=reason or 'CANDIDATE',chart=[list(r) for r in list(self.history[s])[-300:]]))
        return dict(at_ms=now,catalog=len(self.catalog),wide_fresh=sum(bool(h and now-h[-1][0]<=10000) for h in self.history.values()),deep_requested=len(selected),fresh_books=sum(now-b['received_ms']<=2000 for b in self.books.values()),counts=dict(self.counts),rejections=dict(self.rejections),leaders=leaders,positions=list(self.positions.values()),closed=list(self.closed),realized_net_ex_funding=self.realized,all_in_net=None,fee_basis=self.cfg['fee_basis'],config=self.cfg,channels=self.channel_state,orders=False)
