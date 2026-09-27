"""Causal, taker-only short execution simulator; never sends an order.
All scenarios are separate paper portfolios. Decimal lot flooring, actual book VWAP,
entry+exit fees and hourly funding model. Missing funding => total net is null.
"""
from __future__ import annotations
from collections import Counter,defaultdict,deque
from dataclasses import dataclass,field
import math
from books import number,step_down
POLICIES={
 'STOP50_TRAIL35':(50.,50.,35.,900000),
 'STOP75_TRAIL50':(75.,75.,50.,900000),
 'STOP100_TRAIL75':(100.,100.,75.,1800000),
}
ROUTES=('ROTA','HYPERLIQUID','LIGHTER')
HOUR=3600000

def quantile(xs,p):
 if not xs:return None
 ys=sorted(xs);return ys[round((len(ys)-1)*p)]

def hour_boundaries(start,end):
 return list(range((start//HOUR+1)*HOUR,end+1,HOUR)) if end>=start else []

@dataclass
class Position:
 id:str;episode_id:str;symbol:str;venue:str;scenario:str;policy:str;qty:float;opened_ms:int;entry:dict;fee_entry:float
 mfe_bps:float=0.;mae_bps:float=0.;exit_request_ms:int|None=None;exit_reason:str|None=None;closed_ms:int|None=None;exit:dict|None=None;fee_exit:float=0.;funding:dict=field(default_factory=dict)
 def gross(self,fill):return self.qty*(self.entry['vwap']-fill['vwap'])
 def base_net(self,fill,exit_fee):return self.gross(fill)-self.fee_entry-exit_fee
 def funding_total(self,end):
  hours=hour_boundaries(self.opened_ms,end);missing=[t for t in hours if t not in self.funding]
  # A known zero is only legitimate when no payment boundary was crossed.
  return (sum(self.funding.values()) if not missing else None),missing

class ForwardDistributions:
 """Perp-only, non-overlapping descriptive outcomes. Not a calibrated forecast."""
 def __init__(self,emit):self.pending={};self.outcomes=defaultdict(lambda:deque(maxlen=256));self.emit=emit;self.last={};self.censored=0
 def update(self,key,book,now,fee):
  ready,_=book.ready(now)
  if not ready:return
  mid=book.mid();prev=self.last.get(key);self.last[key]=(now,mid)
  for h in (30000,60000,300000,600000,1800000):
   ident=key+(h,);s=self.pending.get(ident)
   if s is not None:
    due=s['start']+h
    if now>=due:
     # Right-censored if there was an observation gap, never a negative outcome.
     valid=not s['gap'] and prev is not None and 0<=due-prev[0]<=2000
     if valid:
      value=10000*(s['price']-prev[1])/s['price']-2*fee
      self.outcomes[ident].append(value);self.emit('forward_labels',{'venue':key[0],'symbol':key[1],'horizon_ms':h,'issued_ms':s['start'],'available_ms':now,'mid_move_minus_scenario_fees_bps':value,'valid':True,'not_executable':True,'not_calibrated_forecast':True,'independent_within_horizon':True})
     else:self.censored+=1;self.emit('forward_labels',{'venue':key[0],'symbol':key[1],'horizon_ms':h,'valid':False,'label':None,'reason':'BOOK_OBSERVATION_GAP','available_ms':now})
     self.pending.pop(ident);s=None
    elif prev and now-prev[0]>2000:s['gap']=True
   if s is None:self.pending[ident]={'start':now,'price':mid,'gap':False}
 def finalize(self,now):
  for (venue,symbol,h),s in self.pending.items():self.emit('forward_labels',{'venue':venue,'symbol':symbol,'horizon_ms':h,'valid':False,'label':None,'reason':'SESSION_END_HORIZON_INCOMPLETE','issued_ms':s['start'],'available_ms':now})
  self.censored+=len(self.pending);self.pending.clear()
 def summary(self):
  return [{'venue':v,'symbol':s,'horizon_s':h//1000,'n':len(a),'p10_bps':quantile(a,.1),'median_bps':quantile(a,.5),'p90_bps':quantile(a,.9),'p_positive_descriptive':sum(x>0 for x in a)/len(a) if a else None,'forecast_validated':False} for (v,s,h),a in self.outcomes.items()]

class Engine:
 def __init__(self,books,config,emit=None):
  self.books=books;self.cfg=config;self.emit=emit or (lambda *args:None);self.positions={};self.orders=[];self.seen=set();self.counts=Counter();self.last_gate={};self.events=deque(maxlen=80);self.curves={p:deque(maxlen=3601) for p in POLICIES};self.used={};self.last_curve=0;self.finalized=False;self.index_history=defaultdict(lambda:deque(maxlen=15000));self.settled_rates={};self.forward=ForwardDistributions(self.emit)
  if config.get('paper_only') is not True or config.get('orders') is not False:raise ValueError('PAPER_ONLY_REQUIRED')
 def record(self,kind,row):
  row={'schema':'belial_t3_perp_'+kind+'_v1','paper_only':True,'orders':False,'signal_engine_feedback':False,**row};self.emit(kind,row)
  if kind in ('orders','trades','signals'):self.events.append(row)
 def fee(self,venue):return float(self.cfg[venue]['taker_bps'])
 def delay(self,venue):return int(self.cfg['network_delay_assumed_ms'])+int(self.cfg[venue]['venue_delay_ms'])
 def position_key(self,episode,scenario,policy):return episode+'|'+scenario+'|'+policy
 def equity_available(self,scenario,policy,now):
  cash=float(self.cfg['capital_usd_per_scenario']);reserved=0.
  for p in self.positions.values():
   if p.scenario!=scenario or p.policy!=policy:continue
   if p.closed_ms is None:reserved+=p.entry['quote'];cash-=p.fee_entry
   else:
    f,_=p.funding_total(p.closed_ms)
    # Unknown funding reserves an extra configurable cost cushion, never counted as profit.
    cash+=p.base_net(p.exit,p.fee_exit)+(f if f is not None else -p.entry['quote']*.01)
  reserved+=sum(o['reserved'] for o in self.orders if o['kind']=='ENTRY' and o['scenario']==scenario and o['policy']==policy)
  return cash-reserved
 def route_quote(self,venue,symbol,now,qty=None):
  b=self.books.books.get((venue,symbol))
  if b is None:raise ValueError('NO_PERP_MARKET')
  ready,reason=b.ready(now,int(self.cfg['book_max_age_ms']))
  if not ready:raise ValueError(reason)
  mid=b.mid();qty=step_down(float(self.cfg['notional_usd'])/mid,b.meta['size_step']) if qty is None else qty
  if qty<b.meta['min_size']:raise ValueError('BELOW_MIN_SIZE')
  sell=b.fill('SELL',qty,now);buy=b.fill('BUY',qty,now)
  if min(sell['quote'],buy['quote'])<b.meta['min_notional']:raise ValueError('BELOW_MIN_NOTIONAL')
  fees=(sell['quote']+buy['quote'])*self.fee(venue)/10000
  spread_depth=(buy['vwap']-sell['vwap'])*qty;bps=(spread_depth+fees)/sell['quote']*10000
  stress=float(self.cfg['latency_stress_bps'])
  return {'venue':venue,'qty':qty,'entry_quote':sell,'exit_quote_now':buy,'roundtrip_cost_bps':bps,'stress_bps':stress,'cost_with_stress_bps':bps+stress,'assumed_delay_ms':self.delay(venue),'current_funding_hourly_decimal':b.context.get('funding_hourly_decimal'),'cost_gate_pass':bps+stress<=float(self.cfg['max_roundtrip_cost_bps']),'edge_gate':'NOT_VALIDATED','gate_basis':'COST_ONLY_NOT_EXPECTED_PROFIT'}
 def signal(self,row,now):
  if self.finalized or row.get('kind')!='EPISODE_OPEN':return
  episode=str(row.get('episode_id') or '');symbol=str(row.get('symbol') or '')
  if not episode or episode in self.seen:return
  self.seen.add(episode);published=int(row.get('at_unix_ms') or 0)
  if row.get('paper_only') is not True or row.get('orders') is not False:raise ValueError('UNSAFE_SIGNAL_CONTRACT')
  if published<=0 or published>now or now-published>30000:
   self.counts['stale_signals']+=1;self.record('signals',{'at_ms':now,'episode':episode,'symbol':symbol,'status':'SIGNAL_STALE_OR_FUTURE'});return
  choices=[];errors={};diagnostics={}
  for venue in ('hyperliquid','lighter'):
   try:
    q=self.route_quote(venue,symbol,now)
    diagnostics[venue]=dict(q,max_roundtrip_cost_bps=float(self.cfg['max_roundtrip_cost_bps']))
    if q['cost_gate_pass']:choices.append(q)
    else:errors[venue]='COST_GATE_REJECTED'
   except ValueError as e:errors[venue]=str(e)
  chosen=min(choices,key=lambda q:(q['cost_with_stress_bps'],q['assumed_delay_ms'])) if choices else None
  self.last_gate[symbol]={'at_ms':now,'status':'PAPER_COST_GATE_PASS' if chosen else 'BLOCKED','routes':choices,'route_diagnostics':diagnostics,'reasons':errors,'selected':chosen['venue'] if chosen else None,'edge_gate':'NOT_VALIDATED','n2_approved_for_decisions':False}
  self.record('signals',{'at_ms':now,'episode':episode,'symbol':symbol,'status':self.last_gate[symbol]['status'],'selected':chosen['venue'] if chosen else None,'reasons':errors,'route_diagnostics':diagnostics,'spot_is_sensor_only':True})
  self.counts['signals']+=1
  for q in choices:
   scenarios=[q['venue'].upper()]+(['ROTA'] if chosen and q['venue']==chosen['venue'] else [])
   for scenario in scenarios:
    for policy in POLICIES:
     reserved=q['entry_quote']['quote']*(1+(self.fee(q['venue'])+float(self.cfg['latency_stress_bps']))/10000)
     if self.equity_available(scenario,policy,now)<reserved:self.counts['capital_rejections']+=1;continue
     order={'kind':'ENTRY','id':self.position_key(episode,scenario,policy),'episode':episode,'symbol':symbol,'venue':q['venue'],'scenario':scenario,'policy':policy,'qty':q['qty'],'requested_ms':now,'eligible_ms':now+self.delay(q['venue']),'expires_ms':now+self.delay(q['venue'])+int(self.cfg['order_expiry_ms']),'reserved':reserved,'decision_book_sequence':q['entry_quote']['book_sequence'],'generation':q['entry_quote']['generation'],'decision_vwap':q['entry_quote']['vwap'],'cost_at_decision_bps':q['cost_with_stress_bps']}
     self.orders.append(order);self.record('orders',{'at_ms':now,'status':'ENTRY_PENDING_LATENCY',**order})
 def _consume(self,b,side,qty,scenario,policy,now):
  key=(b.meta['venue'],b.meta['symbol'],scenario,policy,b.generation,b.sequence,side);used=self.used.get(key,{})
  fill=b.fill(side,qty,now,used,int(self.cfg['book_max_age_ms']))
  # Reset ledger only on a new book revision; portfolios are independent.
  self.used={k:v for k,v in self.used.items() if k[:2]!=(key[:2]) or k[4:6]==key[4:6]}
  if key not in self.used:self.used[key]={}
  for px,q in fill['levels']:self.used[key][px]=self.used[key].get(px,0.)+q
  return fill
 def request_exit(self,p,now,reason):
  if p.exit_request_ms is not None:return
  p.exit_request_ms=now;p.exit_reason=reason;delay=self.delay(p.venue)
  self.orders.append({'kind':'EXIT','id':p.id,'symbol':p.symbol,'venue':p.venue,'scenario':p.scenario,'policy':p.policy,'qty':p.qty,'requested_ms':now,'eligible_ms':now+delay,'expires_ms':now+delay+int(self.cfg['order_expiry_ms']),'reserved':0.})
  self.record('orders',{'at_ms':now,'id':p.id,'kind':'EXIT','status':'EXIT_PENDING_LATENCY','reason':reason,'eligible_ms':now+delay})
 def on_book(self,key,now):
  if self.finalized:return
  b=self.books.books[key];ready,_=b.ready(now,int(self.cfg['book_max_age_ms']))
  if not ready:return
  self.forward.update(key,b,now,self.fee(key[0]))
  # Only a NEW quote received after latency deadline is used for execution.
  for order in list(self.orders):
   if (order['venue'],order['symbol'])!=key:continue
   if now>order['expires_ms']:continue
   if order['kind']=='ENTRY' and b.generation!=order['generation']:
    self.orders.remove(order);self.counts['connection_cancelled_entries']+=1;self.record('orders',{'at_ms':now,'id':order['id'],'status':'ENTRY_CANCELLED_CONNECTION_CHANGED'});continue
   if b.received_ms<order['eligible_ms'] or now<order['eligible_ms']:continue
   try:
    if order['kind']=='ENTRY':
     q=self.route_quote(key[0],key[1],now,order['qty'])
     if not q['cost_gate_pass']:raise ValueError('COST_GATE_CHANGED_DURING_LATENCY')
     # Fill original quantity, not a resized position chosen with future prices.
     trial=b.fill('SELL',order['qty'],now,max_age=int(self.cfg['book_max_age_ms']))
     fee=trial['quote']*self.fee(key[0])/10000
     if trial['quote']<b.meta['min_notional']:raise ValueError('BELOW_MIN_NOTIONAL_AT_FILL')
     if trial['quote']+fee>self.equity_available(order['scenario'],order['policy'],now)+order['reserved']:raise ValueError('CAPITAL_CHANGED_DURING_LATENCY')
     fill=self._consume(b,'SELL',order['qty'],order['scenario'],order['policy'],now)
     p=Position(order['id'],order['episode'],key[1],key[0],order['scenario'],order['policy'],order['qty'],now,fill,fill['quote']*self.fee(key[0])/10000)
     self.positions[p.id]=p;self.counts['entries']+=1
     self.record('orders',{'at_ms':now,'id':p.id,'status':'PAPER_ENTRY_FILLED','requested_ms':order['requested_ms'],'eligible_ms':order['eligible_ms'],'fill':fill,'fee_usd':p.fee_entry,'decision_to_fill_ms':now-order['requested_ms'],'decision_to_fill_price_bps':10000*(order['decision_vwap']-fill['vwap'])/order['decision_vwap']})
    else:
     p=self.positions[order['id']];fill=self._consume(b,'BUY',p.qty,p.scenario,p.policy,now);p.exit=fill;p.fee_exit=fill['quote']*self.fee(p.venue)/10000;p.closed_ms=now;self.counts['closed']+=1
     self.reconcile_funding(p,now);self.record('trades',self.position_view(p,now))
    self.orders.remove(order)
   except ValueError as e:
    order['last_block']=str(e)
  for p in self.positions.values():
   if p.closed_ms is not None or (p.venue,p.symbol)!=key or p.exit_request_ms is not None:continue
   try:
    fill=b.fill('BUY',p.qty,now,max_age=int(self.cfg['book_max_age_ms']));gross=p.gross(fill)/p.entry['quote']*10000
   except ValueError:continue
   p.mfe_bps=max(p.mfe_bps,gross);p.mae_bps=max(p.mae_bps,-gross);stop,activate,give,hold=POLICIES[p.policy]
   if gross<=-stop:self.request_exit(p,now,'STOP')
   elif p.mfe_bps>=activate and p.mfe_bps-gross>=give:self.request_exit(p,now,'TRAIL_GIVEBACK')
   elif now-p.opened_ms>=hold:self.request_exit(p,now,'MAX_HOLD')
 def record_context(self,key,book,now):
  ctx=book.context;index=ctx.get('index_price')
  if index is not None and book.context_ms and now-book.context_ms<=2000:
   hist=self.index_history[key]
   if not hist or hist[-1][0]!=book.context_ms:hist.append((book.context_ms,float(index)))
  if key[0]=='lighter' and ctx.get('settled_timestamp') and ctx.get('settled_hourly_decimal') is not None:
   self.funding_rate(key,int(ctx['settled_timestamp']),float(ctx['settled_hourly_decimal']),now,'LIGHTER_MARKET_STATS_PERCENT')
 def funding_rate(self,key,at,rate,known_ms,source):
  if at%HOUR!=0 or at>known_ms or not math.isfinite(rate):return
  self.settled_rates[(key,at)]={'rate':rate,'known_ms':known_ms,'source':source}
  for p in self.positions.values():
   if (p.venue,p.symbol)==key:self.reconcile_funding(p,known_ms)
 def reconcile_funding(self,p,now):
  end=p.closed_ms or now;key=p.venue,p.symbol
  for boundary in hour_boundaries(p.opened_ms,end):
   if boundary in p.funding:continue
   rate=self.settled_rates.get((key,boundary))
   if not rate:continue
   # Only an index actually observed at/before settlement, max 2s old.
   indices=[(t,px) for t,px in self.index_history[key] if 0<=boundary-t<=int(self.cfg['funding']['max_index_age_ms'])]
   if not indices:continue
   t,px=indices[-1];payment=p.qty*px*rate['rate'];p.funding[boundary]=payment
   self.record('funding',{'id':p.id,'symbol':p.symbol,'venue':p.venue,'settlement_ms':boundary,'known_ms':now,'rate_decimal':rate['rate'],'index_observed_ms':t,'index':px,'paper_cashflow_usd':payment,'source':rate['source'],'status':'OBSERVED_RATE_INDEX_MODEL_NOT_ACTUAL_ACCOUNT_SETTLEMENT'})
 def tick(self,now):
  if self.finalized:return
  for order in list(self.orders):
   if now<=order['expires_ms']:continue
   self.orders.remove(order);self.counts['expired_orders']+=1;self.record('orders',{'at_ms':now,'id':order['id'],'status':'PAPER_ORDER_EXPIRED','reason':order.get('last_block','NO_FRESH_QUOTE_AFTER_LATENCY'),'kind':order['kind']})
   if order['kind']=='EXIT':
    p=self.positions[order['id']];p.exit_request_ms=None;self.request_exit(p,now,p.exit_reason or 'EXIT_RETRY')
    # Position is still OPEN, never invent a closing fill on a stale book.
  for p in self.positions.values():
   if p.closed_ms is None:
    self.reconcile_funding(p,now)
    if p.exit_request_ms is None and now-p.opened_ms>=POLICIES[p.policy][3]:self.request_exit(p,now,'MAX_HOLD_WAITING_BOOK')
  if now-self.last_curve>=1000:
   for policy in POLICIES:
    total=self.aggregate('ROTA',policy,now);self.curves[policy].append([now,total['total_net_usd']])
   self.last_curve=now
 def position_view(self,p,now):
  end=p.closed_ms or now;fill=p.exit;status='CLOSED' if fill else 'OPEN';unknown_reason=None
  if fill is None:
   book=self.books.books.get((p.venue,p.symbol))
   try:fill=book.fill('BUY',p.qty,now,max_age=int(self.cfg['book_max_age_ms'])) if book else None
   except ValueError as e:unknown_reason=str(e)
  fees_exit=fill['quote']*self.fee(p.venue)/10000 if fill else None
  gross=p.gross(fill) if fill else None;base=p.base_net(fill,fees_exit) if fill else None;fund,missing=p.funding_total(end)
  return {'id':p.id,'episode_id':p.episode_id,'symbol':p.symbol,'venue':p.venue,'scenario':p.scenario,'policy':p.policy,'status':status,'qty':p.qty,'opened_ms':p.opened_ms,'closed_ms':p.closed_ms,'hold_ms':end-p.opened_ms,'entry_vwap':p.entry['vwap'],'exit_or_close_now_vwap':fill['vwap'] if fill else None,'notional_usd':p.entry['quote'],'gross_usd':gross,'entry_fee_usd':p.fee_entry,'exit_fee_usd':fees_exit,'funding_model_usd':fund,'missing_funding_boundaries':missing,'net_ex_funding_usd':base,'net_usd':base+fund if base is not None and fund is not None else None,'exit_reason':p.exit_reason,'exit_pending':p.exit_request_ms is not None and p.closed_ms is None,'mfe_bps':p.mfe_bps,'mae_bps':p.mae_bps,'quote_error':unknown_reason,'exit_slippage_bps':fill['slippage_vs_top_bps'] if fill else None,'entry_slippage_bps':p.entry['slippage_vs_top_bps'],'hold_decision_basis':'FROZEN_POLICY_ON_PERP_QUOTES','expected_hold_net_distribution':'NOT_CALIBRATED','funding_status':'MODEL_COMPLETE' if fund is not None else 'UNKNOWN_NOT_ZERO','fees_status':'PUBLIC_PROFILE_SCENARIO_NOT_USER_FEE','not_strategy_pnl':True,'paper_only':True}
 def aggregate(self,scenario,policy,now):
  rows=[self.position_view(p,now) for p in self.positions.values() if p.scenario==scenario and p.policy==policy];closed=[r for r in rows if r['status']=='CLOSED'];opened=[r for r in rows if r['status']=='OPEN']
  def total(xs,k):return sum(r[k] for r in xs) if all(r[k] is not None for r in xs) else None
  realized=total(closed,'net_usd');unrealized=total(opened,'net_usd');complete=realized is not None and unrealized is not None
  return {'scenario':scenario,'policy':policy,'entries':len(rows),'closed':len(closed),'open':len(opened),'realized_net_usd':realized,'close_now_net_usd':unrealized,'total_net_usd':realized+unrealized if complete else None,'total_ex_funding_usd':total(rows,'net_ex_funding_usd'),'fees_entry_usd':sum(r['entry_fee_usd'] for r in rows),'fees_exit_usd':total(rows,'exit_fee_usd'),'funding_usd':total(rows,'funding_model_usd'),'gross_usd':total(rows,'gross_usd'),'capital_usd':self.cfg['capital_usd_per_scenario'],'return_on_initial_capital_pct':(realized+unrealized)/self.cfg['capital_usd_per_scenario']*100 if complete else None,'missing_net_rows':sum(r['net_usd'] is None for r in rows),'wins':sum(r['net_usd'] is not None and r['net_usd']>0 for r in closed),'losses':sum(r['net_usd'] is not None and r['net_usd']<0 for r in closed),'pending':sum(o['scenario']==scenario and o['policy']==policy for o in self.orders)}
 def finalize(self,now):
  # End at 60m: no invented fills, no extra 30-minute capture hidden from the user.
  self.tick(now)
  for o in self.orders:self.record('orders',{'at_ms':now,'id':o['id'],'status':'CENSORED_SESSION_END','kind':o['kind']})
  self.orders.clear();self.forward.finalize(now);self.finalized=True
  for p in self.positions.values():
   if p.closed_ms is None:self.record('trades',{'at_ms':now,'status':'OPEN_CENSORED_SESSION_END','position':self.position_view(p,now)})
 def snapshot(self,now):
  return {'schema':'belial_t3_paper_state_v1','at_ms':now,'complete':self.finalized,'counts':dict(self.counts),'scenarios':[self.aggregate(s,p,now) for s in ROUTES for p in POLICIES],'positions':[self.position_view(p,now) for p in self.positions.values()][-100:],'gate':self.last_gate,'recent':list(self.events)[-20:],'curves':{p:list(c) for p,c in self.curves.items()},'forward_descriptive':self.forward.summary(),'forward_censored':self.forward.censored,'orders_pending':len(self.orders),'not_strategy_pnl':True,'paper_only':True,'orders':False,'transactions_submitted':False,'signal_engine_feedback':False}
