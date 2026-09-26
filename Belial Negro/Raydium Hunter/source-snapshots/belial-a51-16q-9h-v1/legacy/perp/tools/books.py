"""Validated public books. Snapshots replace; Lighter deltas require nonce continuity.
Prices and quantities are normalized to one underlying token, never to TOKEN/SOL.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal,ROUND_FLOOR,InvalidOperation
import math,time

def number(x,positive=False):
 if isinstance(x,bool) or x is None:raise ValueError('NUMBER_MISSING')
 v=float(x)
 if not math.isfinite(v) or (positive and v<=0):raise ValueError('NUMBER_INVALID')
 return v

def step_down(q,step):
 qd,sd=Decimal(str(q)),Decimal(str(step))
 if not qd.is_finite() or not sd.is_finite() or sd<=0 or qd<0:raise ValueError('SIZE_INVALID')
 return float((qd/sd).to_integral_value(rounding=ROUND_FLOOR)*sd)

@dataclass
class Book:
 meta:dict
 bids:dict=field(default_factory=dict)
 asks:dict=field(default_factory=dict)
 nonce:int|None=None
 generation:int=0
 sequence:int=0
 received_ms:int=0
 exchange_ms:int=0
 valid:bool=False
 reason:str='WAITING_SNAPSHOT'
 updates:int=0
 gaps:int=0
 last_bbo:dict=field(default_factory=dict)
 context:dict=field(default_factory=dict)
 context_ms:int=0
 _sorted:tuple|None=None
 def reset(self,reason):
  self.valid=False;self.reason=reason;self.nonce=None;self.bids.clear();self.asks.clear();self._sorted=None
 def levels(self):
  if self._sorted is None:
   self._sorted=(sorted(self.bids.items(),reverse=True),sorted(self.asks.items()))
  return self._sorted
 def ready(self,now,max_age=2000,future=500):
  if not self.valid:return False,self.reason
  if self.received_ms>now+future or self.exchange_ms>now+future:return False,'CLOCK_SKEW'
  if now-self.received_ms>max_age or now-self.exchange_ms>max_age:return False,'BOOK_STALE'
  return True,'OK'
 def _apply(self,rows,target,pxfield,szfield):
  if not isinstance(rows,list) or len(rows)>200000:raise ValueError('BOOK_LEVEL_SCHEMA_OR_CAPACITY')
  mult=number(self.meta['multiplier'],True)
  for row in rows:
   p=Decimal(str(row[pxfield]))/Decimal(str(mult));q=number(row[szfield])*mult
   if not p.is_finite() or p<=0 or q<0:raise ValueError('INVALID_PRICE_OR_SIZE')
   if q==0:target.pop(p,None)
   else:target[p]=q
  if len(target)>200000:raise ValueError('BOOK_CAPACITY')
 def update_hl(self,data,received_ms,generation):
  ts=int(data['time'])
  # Duplicate or time-regressing snapshots never re-arm freshness or fill pending orders.
  if self.valid and generation==self.generation and ts<=self.exchange_ms:return False
  self.bids={};self.asks={}
  self._apply(data['levels'][0],self.bids,'px','sz');self._apply(data['levels'][1],self.asks,'px','sz')
  self.exchange_ms=ts;self.generation=generation;self.finish(received_ms);return True
 def update_li(self,data,received_ms,generation):
  payload=data['order_book'];snapshot=str(data.get('type','')).startswith('subscribed/')
  nonce=int(payload['nonce']);begin=payload.get('begin_nonce')
  if snapshot:
   self.bids={};self.asks={};self.generation=generation
  else:
   if generation!=self.generation or self.nonce is None:raise ValueError('SNAPSHOT_REQUIRED')
   if nonce<self.nonce:raise ValueError('NONCE_REGRESSION')
   if begin is None or int(begin)!=self.nonce:
    self.gaps+=1;raise ValueError('NONCE_GAP')
  self._apply(payload.get('bids',[]),self.bids,'price','size');self._apply(payload.get('asks',[]),self.asks,'price','size')
  self.nonce=nonce
  # last_updated_at is microseconds, timestamp is server envelope milliseconds.
  updated=payload.get('last_updated_at') or data.get('last_updated_at')
  self.exchange_ms=int(updated)//1000 if updated else int(data['timestamp'])
  self.finish(received_ms);return True
 def finish(self,received_ms):
  self._sorted=None
  if not self.bids or not self.asks:raise ValueError('EMPTY_BOOK')
  if max(self.bids)>=min(self.asks):raise ValueError('CROSSED_OR_LOCKED_BOOK')
  self.received_ms=received_ms;self.valid=True;self.reason='OK';self.sequence+=1;self.updates+=1
 def mid(self):
  if not self.valid:return None
  return float((max(self.bids)+min(self.asks))/2)
 def fill(self,side,qty,now,used=None,max_age=2000):
  ok,reason=self.ready(now,max_age)
  if not ok:raise ValueError(reason)
  qty=number(qty,True);levels=self.levels()[0 if side=='SELL' else 1];remain=qty;cash=0.;taken=[];used=used or {}
  for price,visible in levels:
   available=max(0.,visible-used.get(str(price),0.));q=min(remain,available)
   if q>0:cash+=float(price)*q;remain-=q;taken.append((str(price),q))
   if remain<=max(1e-12,qty*1e-12):break
  if remain>max(1e-12,qty*1e-12):raise ValueError('INSUFFICIENT_VISIBLE_DEPTH')
  top=float(levels[0][0]);vwap=cash/qty
  return {'side':side,'qty':qty,'quote':cash,'vwap':vwap,'top':top,'levels':taken,'book_sequence':self.sequence,'generation':self.generation,'book_received_ms':self.received_ms,'book_exchange_ms':self.exchange_ms,'fill_model_ms':now,'slippage_vs_top_bps':max(0.,(top-vwap)/top*10000 if side=='SELL' else (vwap-top)/top*10000)}
 def view(self,now):
  ok,reason=self.ready(now);bid,ask=self.levels() if self.valid else ([],[]);mid=self.mid()
  return {'venue':self.meta['venue'],'symbol':self.meta['symbol'],'market':self.meta['market'],'status':reason,'ready':ok,'age_ms':max(0,now-self.received_ms) if self.received_ms else None,'source_age_ms':now-self.exchange_ms if self.exchange_ms else None,'spread_bps':float((ask[0][0]-bid[0][0]))/mid*10000 if bid and ask else None,'bid':float(bid[0][0]) if bid else None,'ask':float(ask[0][0]) if ask else None,'bids':[[float(p),q] for p,q in bid[:8]],'asks':[[float(p),q] for p,q in ask[:8]],'updates':self.updates,'gaps':self.gaps,'sequence':self.sequence,'multiplier':self.meta['multiplier'],'context':self.context,'context_age_ms':now-self.context_ms if self.context_ms else None,'bbo':self.last_bbo}

class Books:
 def __init__(self,manifest,reconnect=None):
  self.books={(m['venue'],m['symbol']):Book(m) for m in manifest['markets']};self.market_index={(m['venue'],str(m['market'])):(m['venue'],m['symbol']) for m in manifest['markets']};self.reconnect=reconnect or (lambda v:None);self.errors=[];self.generations={};self.last_reset={};self.transport={}
 def request_reset(self,venue,now):
  if now-self.last_reset.get(venue,-100000)>=5000:self.last_reset[venue]=now;self.reconnect(venue)
 def ingest(self,envelope):
  venue=envelope['venue'];received=int(envelope['received_ns'])//1000000;gen=int(envelope['generation']);updated=[];self.generations[venue]=gen
  if 'transport' in envelope:
   self.transport[venue]=envelope['transport']
   for (v,s),book in self.books.items():
    if v==venue:book.reset(envelope['transport']);book.generation=gen
   return []
  data=envelope.get('data') or {};channel=str(data.get('channel',''));payload=data.get('data') or {};key=None
  if venue=='hyperliquid':
   if channel in ('l2Book','activeAssetCtx'):key=self.market_index.get((venue,str(payload.get('coin'))))
  else:
   if ':' in channel:key=self.market_index.get((venue,channel.split(':',1)[1]))
  if key not in self.books:return []
  b=self.books[key]
  try:
   if gen!=b.generation:b.reset('NEW_CONNECTION_SNAPSHOT_REQUIRED');b.generation=gen
   if channel=='l2Book':
    if b.update_hl(payload,received,gen):updated.append(key)
   elif channel.startswith('order_book:'):
    if b.update_li(data,received,gen):updated.append(key)
   elif channel=='activeAssetCtx':
    ctx=payload.get('ctx') or {};mult=b.meta['multiplier']
    b.context={'funding_hourly_decimal':number(ctx['funding']) if ctx.get('funding') is not None else None,'index_price':number(ctx['oraclePx'],True)/mult if ctx.get('oraclePx') else None,'mark_price':number(ctx['markPx'],True)/mult if ctx.get('markPx') else None,'funding_rate_kind':'ESTIMATE_NEXT_HOUR'};b.context_ms=received
   elif channel.startswith('market_stats:'):
    ctx=data['market_stats'];mult=b.meta['multiplier']
    b.context={'funding_hourly_decimal':number(ctx['current_funding_rate'])/100 if ctx.get('current_funding_rate') is not None else None,'settled_hourly_decimal':number(ctx['funding_rate'])/100 if ctx.get('funding_rate') is not None else None,'settled_timestamp':int(ctx.get('funding_timestamp') or 0),'index_price':number(ctx['index_price'],True)/mult if ctx.get('index_price') else None,'mark_price':number(ctx['mark_price'],True)/mult if ctx.get('mark_price') else None,'funding_rate_kind':'PERCENT_API_CONVERTED_TO_DECIMAL'};b.context_ms=received
   elif channel.startswith('ticker:'):
    t=data['ticker'];mult=b.meta['multiplier'];b.last_bbo={'bid':number(t['b']['price'],True)/mult,'ask':number(t['a']['price'],True)/mult,'received_ms':received,'nonce':data.get('nonce'),'display_only':True}
  except (ValueError,KeyError,TypeError,IndexError,InvalidOperation) as e:
   b.reset(str(e));self.errors.append({'at_ms':received,'venue':venue,'symbol':key[1],'error':str(e)});self.errors=self.errors[-100:];self.request_reset(venue,received)
  return updated
