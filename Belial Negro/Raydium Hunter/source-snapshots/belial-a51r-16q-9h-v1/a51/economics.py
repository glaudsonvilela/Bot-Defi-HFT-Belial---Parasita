"""PAPER economics with immutable settlement evidence and explicit unknown costs.
No chain transaction is submitted. Public fee profiles are scenarios, not paid bills.
"""
from __future__ import annotations
import math
from a51.common import add_legacy
add_legacy('perp')
from paper import Engine as LegacyEngine,HOUR,hour_boundaries,POLICIES
COST_KEYS=('network_entry_usd','network_exit_usd','movements_allocated_usd_per_closed_trade','other_usd_per_closed_trade')

def validate_costs(value):
    if not isinstance(value,dict) or value.get('schema')!='belial_a2_external_costs_v1':raise ValueError('COST_PROFILE_SCHEMA')
    if value.get('basis') not in ('USER_ASSUMPTIONS','DOCUMENTED_SCENARIO','UNKNOWN'):raise ValueError('COST_PROFILE_BASIS')
    for venue in ('hyperliquid','lighter'):
        if not isinstance(value.get(venue),dict) or set(value[venue])!=set(COST_KEYS):raise ValueError('COST_PROFILE_FIELDS:'+venue)
        for k,v in value[venue].items():
            if v is not None and (isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or v<0):raise ValueError('INVALID_COST:'+k)
    if value.get('basis')=='UNKNOWN' and any(v is not None for venue in ('hyperliquid','lighter') for v in value[venue].values()):raise ValueError('UNKNOWN_PROFILE_MUST_NOT_DECLARE_COSTS')
    if not isinstance(value.get('description'),str) or not value['description'].strip():raise ValueError('COST_DESCRIPTION_REQUIRED')
    return value

class Engine(LegacyEngine):
    def __init__(self,books,config,emit=None):
        super().__init__(books,config,emit)
        self.pinned_indices={};self.rate_conflicts=set();self.settlement_deadline=None
        profile=config.get('external_costs')
        self.external=validate_costs(profile) if profile is not None else None
    def record_context(self,key,book,now):
        ctx=book.context;index=ctx.get('index_price');t=book.context_ms
        if index is not None and type(t) is int and 0<t<=now and now-t<=2000:
            px=float(index)
            if math.isfinite(px) and px>0:
                boundary=((t+HOUR-1)//HOUR)*HOUR
                # Keep the last observed pre-boundary index independently of the
                # high-frequency deque, which can overflow before funding arrives.
                if 0<=boundary-t<=int(self.cfg['funding']['max_index_age_ms']):
                    current=self.pinned_indices.get((key,boundary))
                    if current is None or t>current[0]:self.pinned_indices[(key,boundary)]=(t,px)
        # Legacy context handling calls the overridden funding_rate below.
        super().record_context(key,book,now)
    def funding_rate(self,key,at,rate,known_ms,source):
        if type(at) is not int or type(known_ms) is not int or at>known_ms or not math.isfinite(rate):return
        if source not in ('HYPERLIQUID_FUNDING_HISTORY','LIGHTER_MARKET_STATS_PERCENT'):return
        boundary=at//HOUR*HOUR;offset=at-boundary
        # Official HL history uses receipt timestamps e.g. hour+76 ms. Retain the
        # original timestamp; only the first 1000 ms map to that same UTC hour.
        if offset>1000:return
        ident=(key,boundary)
        if ident in self.rate_conflicts:return
        prev=self.settled_rates.get(ident)
        if prev is not None:
            if prev['rate']==rate:return
            self.rate_conflicts.add(ident);self.settled_rates.pop(ident,None)
            for p in self.positions.values():
                if (p.venue,p.symbol)==key:p.funding.pop(boundary,None)
            self.record('funding_conflict',dict(venue=key[0],symbol=key[1],settlement_ms=boundary,known_ms=known_ms,status='CONFLICT_UNKNOWN_NOT_ZERO'))
            return
        self.settled_rates[ident]={'rate':float(rate),'known_ms':known_ms,'source':source,'source_timestamp_ms':at,'boundary_ms':boundary}
        self.record('settled_rate',dict(venue=key[0],symbol=key[1],settlement_ms=boundary,source_timestamp_ms=at,known_ms=known_ms,rate_decimal=rate,source=source,not_actual_account_settlement=True))
        for p in self.positions.values():
            if (p.venue,p.symbol)==key:self.reconcile_funding(p,known_ms)
    def reconcile_funding(self,p,now):
        end=p.closed_ms if p.closed_ms is not None else min(now,self.settlement_deadline) if self.settlement_deadline is not None else now
        key=p.venue,p.symbol
        for boundary in hour_boundaries(p.opened_ms,end):
            if boundary in p.funding:continue
            rate=self.settled_rates.get((key,boundary));index=self.pinned_indices.get((key,boundary))
            if rate is None or index is None:continue
            t,px=index
            if not 0<=boundary-t<=int(self.cfg['funding']['max_index_age_ms']):continue
            payment=p.qty*px*rate['rate'];p.funding[boundary]=payment
            self.record('funding',dict(id=p.id,symbol=p.symbol,venue=p.venue,settlement_ms=boundary,known_ms=now,rate_decimal=rate['rate'],index_observed_ms=t,index=px,paper_cashflow_usd=payment,source=rate['source'],source_timestamp_ms=rate['source_timestamp_ms'],status='OBSERVED_RATE_INDEX_MODEL_NOT_ACTUAL_ACCOUNT_SETTLEMENT'))
    def finalize(self,now):
        self.settlement_deadline=now
        super().finalize(now)
    def position_view(self,p,now):
        row=super().position_view(p,now)
        costs=(self.external or {}).get(p.venue,{})
        missing=[k for k in COST_KEYS if costs.get(k) is None]
        known=sum(float(costs[k]) for k in COST_KEYS if costs.get(k) is not None)
        # For OPEN positions this is a close-now cost scenario, not a paid bill.
        complete=not missing and row['net_usd'] is not None
        row.update(external_costs=dict(costs),external_known_subtotal_usd=known if len(missing)<len(COST_KEYS) else None,external_known_item_count=len(COST_KEYS)-len(missing),external_missing=missing,
            all_in_model_usd=row['net_usd']-known if complete else None,
            all_in_status='COMPLETE_DECLARED_MODEL_NOT_REAL_EXPENSE' if complete else 'UNKNOWN_COSTS_OR_FUNDING_NOT_ZERO',
            cost_profile_basis=(self.external or {}).get('basis','UNKNOWN'),
            solana_spot_transaction_cost_usd=0.,solana_spot_cost_reason='NOT_APPLICABLE_READ_ONLY_SENSOR',
            external_costs_affect_entry_gate=False,not_real_account_pnl=True)
        stop,activation,giveback,hold=POLICIES[p.policy]
        current_bps=row['gross_usd']/row['notional_usd']*10000 if row['gross_usd'] is not None and row['notional_usd']>0 else None
        row.update(trailing_activation_bps=activation,trailing_armed=p.mfe_bps>=activation,
                   trailing_giveback_bps=giveback,stop_bps=stop,max_hold_ms=hold,
                   gap_to_activation_bps=max(0.,activation-p.mfe_bps),
                   mfe_gross_usd=row['notional_usd']*p.mfe_bps/10000,
                   current_gross_bps=current_bps,
                   gross_giveback_bps=max(0.,p.mfe_bps-current_bps) if current_bps is not None else None,
                   protection_is_display_of_frozen_rule=True)
        return row
    def snapshot(self,now):
        value=super().snapshot(now)
        active=[p for p in self.positions.values() if p.closed_ms is None]
        closed=[p for p in self.positions.values() if p.closed_ms is not None]
        value['positions']=[self.position_view(p,now) for p in active+closed[-100:]]
        value['all_open_positions_included']=True
        return value
    def aggregate(self,scenario,policy,now):
        row=super().aggregate(scenario,policy,now)
        positions=[self.position_view(p,now) for p in self.positions.values() if p.scenario==scenario and p.policy==policy]
        complete=bool(positions) and all(p['all_in_model_usd'] is not None for p in positions)
        row.update(all_in_model_usd=sum(p['all_in_model_usd'] for p in positions) if complete else None,
            external_known_subtotal_usd=sum(p['external_known_subtotal_usd'] or 0.0 for p in positions) if any(p['external_known_item_count'] for p in positions) else None,
            unknown_external_positions=sum(bool(p['external_missing']) for p in positions),
            all_in_status='COMPLETE_DECLARED_MODEL' if complete else 'UNKNOWN_OR_NO_POSITIONS',
            all_in_not_actual_account_pnl=True)
        return row
