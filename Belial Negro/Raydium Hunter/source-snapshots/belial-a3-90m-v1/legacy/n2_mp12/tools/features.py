#!/usr/bin/env python3
from __future__ import annotations
import collections,math
from dataclasses import dataclass
from typing import Iterable

WINDOWS=(1000,3000,5000,10000,30000,60000)
BASE_NAMES=[
'sell_quote_log','sell_reserve_fraction','cluster_size','cluster_wallets','cluster_quote_log',
'impact_score','absorption','actor_latency','inventory_exit_pct','observed_inventory_pool_pct',
'leader_quality','leader_samples_log','pre_return_30s','pre_sell_pressure_30s','regime_bull',
'regime_mixed','regime_bear','signal_age','observed_vs_reference_spread','trigger_quote_reserve',
'cluster_quote_reserve','reference_age','same_wallet_hint','multi_wallet_hint']
for ms in WINDOWS:
    lab=f'{ms//1000}s'
    BASE_NAMES += [f'swap_count_{lab}',f'quote_total_{lab}',f'sell_imbalance_{lab}',
                   f'reference_return_{lab}',f'abs_return_{lab}',f'max_quote_reserve_{lab}']
BASE_NAMES += ['window_1s_empty','window_5s_empty','window_30s_empty','last_interarrival']
assert len(BASE_NAMES)==64

@dataclass(frozen=True)
class Mark:
    t:int; price:float; quote:float; reserve_quote:float; side:str; signature:str=''

def finite(v,default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else float(default)
    except (TypeError,ValueError):return float(default)

def unit(x):return max(-1.0,min(1.0,float(x)))
def squish(x,scale=1.0):return math.tanh(float(x)/float(scale)) if scale else 0.0
def logpos(x,scale=5.0):return unit(math.log1p(max(0.0,finite(x)))/scale)
def short_bps(a,b):return 10000.0*(a-b)/a if a>0 and b>0 else 0.0

def swap_to_mark(row:dict)->Mark|None:
    try:
        t=int(row.get('received_unix_ms') or row.get('writer_observed_unix_ms') or 0)
        p=finite(row.get('reference_post_price'))
        q=finite(row.get('quote_sol'))
        r=finite(row.get('pool_quote_reserve_sol'))
        side=str(row.get('side') or '').upper()
        if t<=0 or p<=0 or q<0 or r<=0 or side not in ('BUY','SELL'):return None
        return Mark(t,p,q,r,side,str(row.get('signature') or ''))
    except Exception:return None

def _asof(hist:list[Mark],t:int)->Mark|None:
    for m in reversed(hist):
        if m.t<=t:return m
    return None

def build_base(exit_row:dict,hist:Iterable[Mark],published_ms:int)->tuple[list[float],dict]:
    rows=[m for m in hist if m.t<=published_ms]
    if not rows:raise ValueError('NO_REFERENCE_HISTORY')
    mark=rows[-1]
    age=max(0,published_ms-mark.t)
    if age>30000:raise ValueError('REFERENCE_TOO_OLD')
    sig=str(exit_row.get('event_id') or '').split('|',1)[0]
    trigger=next((m for m in reversed(rows) if sig and m.signature==sig),mark)
    reserve=max(trigger.reserve_quote,1e-12)
    sell_quote=max(0.0,finite(exit_row.get('sell_quote_sol')))
    cluster_quote=max(0.0,finite(exit_row.get('cluster_sell_quote_sol_10s')))
    cluster_size=max(0.0,finite(exit_row.get('cluster_size')))
    cluster_wallets=max(0.0,finite(exit_row.get('cluster_unique_wallets_10s')))
    actor_lat=max(0.0,finite(exit_row.get('actor_resolution_latency_ms')))
    ev=int(exit_row.get('event_unix_ms') or published_ms)
    signal_age=max(0,published_ms-ev)
    observed=finite(exit_row.get('observed_entry_price'))
    reference=finite(exit_row.get('entry_price')) or mark.price
    spread=10000.0*(observed-reference)/reference if observed>0 and reference>0 else 0.0
    regime=str(exit_row.get('regime') or '').upper()
    x=[
      logpos(sell_quote,5.0),squish(sell_quote/reserve,0.001),squish(cluster_size,5.0),squish(cluster_wallets,5.0),logpos(cluster_quote,5.0),
      squish(finite(exit_row.get('impact_score')),100.0),squish(finite(exit_row.get('absorption_score')),100.0),squish(actor_lat,3000.0),
      squish(finite(exit_row.get('inventory_exit_pct')),100.0),squish(finite(exit_row.get('observed_inventory_pool_pct')),1.0),
      squish(finite(exit_row.get('leader_quality_120s_at_entry')),100.0),logpos(finite(exit_row.get('leader_samples_120s_at_entry')),4.0),
      squish(finite(exit_row.get('pre_return_30s_bps')),100.0),squish(finite(exit_row.get('pre_sell_pressure_30s_pct')),100.0),
      1.0 if regime in ('BULL','UP','POSITIVE') else 0.0,1.0 if regime in ('MIXED','NEUTRAL','') else 0.0,1.0 if regime in ('BEAR','DOWN','NEGATIVE') else 0.0,
      squish(signal_age,30000.0),squish(spread,100.0),squish(trigger.quote/trigger.reserve_quote,0.001),squish(cluster_quote/reserve,0.005),
      squish(age,30000.0),1.0 if cluster_size>=2 else 0.0,1.0 if cluster_wallets>=2 else 0.0,
    ]
    debug={'reference_price':mark.price,'reference_source_ms':mark.t,'reference_age_ms':age,'signal_age_ms':signal_age,'trigger_signature':trigger.signature}
    for ms in WINDOWS:
        ws=[m for m in rows if published_ms-ms<=m.t<=published_ms]
        n=len(ws);total=sum(m.quote for m in ws)
        imb=sum(m.quote*(1.0 if m.side=='SELL' else -1.0) for m in ws)/total if total>0 else 0.0
        back=_asof(rows,published_ms-ms)
        ret=10000.0*(mark.price/back.price-1.0) if back and back.price>0 else 0.0
        absret=0.0
        if len(ws)>=2:
            prev=ws[0].price
            for m in ws[1:]:
                if prev>0 and m.price>0:absret+=abs(10000.0*(m.price/prev-1.0))
                prev=m.price
        mx=max((m.quote/max(m.reserve_quote,1e-12) for m in ws),default=0.0)
        x += [squish(n,25.0 if ms<=10000 else 100.0),logpos(total,8.0),unit(imb),squish(ret,100.0),squish(absret,200.0),squish(mx,0.001)]
    def empty(ms):return 1.0 if not any(published_ms-ms<=m.t<=published_ms for m in rows) else 0.0
    gap=(rows[-1].t-rows[-2].t) if len(rows)>=2 else 60000
    x += [empty(1000),empty(5000),empty(30000),squish(gap,10000.0)]
    if len(x)!=64 or not all(math.isfinite(v) and -1.0000001<=v<=1.0000001 for v in x):raise ValueError('BASE_FEATURE_CONTRACT')
    return [float(max(-1.0,min(1.0,v))) for v in x],debug

def mix32(x:int)->int:
    x &= 0xffffffff
    x ^= x>>16; x=(x*0x7feb352d)&0xffffffff; x ^= x>>15; x=(x*0x846ca68b)&0xffffffff; x ^= x>>16
    return x&0xffffffff

def expanded_feature(base:list[float],j:int)->float:
    if j==0:return 1.0
    if 1<=j<=64:return float(base[j-1])
    k=j-65
    h1=mix32((k*0x9e3779b1+0x85ebca6b)&0xffffffff)
    h2=mix32((k*0xc2b2ae35+0x27d4eb2f)&0xffffffff)
    a=h1&63;b=h2&63
    va=float(base[a]);vb=float(base[b]);mode=(k>>3)&3
    if mode==0:v=va*vb
    elif mode==1:v=va*vb*(0.5+0.5*abs(va-vb))
    elif mode==2:v=va*va*(1.0 if vb>=0 else -1.0)
    else:v=0.5*(va+vb)*abs(va-vb)
    if h1&0x80000000:v=-v
    return max(-1.0,min(1.0,v))

def expand_cpu(base:list[float],dim:int)->list[float]:
    if len(base)!=64 or not 65<=dim<=8192:raise ValueError('EXPANSION_DIM')
    return [expanded_feature(base,j) for j in range(dim)]
