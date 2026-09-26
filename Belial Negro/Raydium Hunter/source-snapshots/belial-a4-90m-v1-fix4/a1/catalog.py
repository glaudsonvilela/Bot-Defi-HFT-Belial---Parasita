"""Per-source capability is not a platform-wide approval or token-risk certificate."""
from __future__ import annotations
from a1.common import SYMBOLS,now_ms

def initial():
    return {s:dict(symbol=s,state='DISCOVERING',identity=False,raw_capture=False,
                   price=False,actor=False,qualified=False,reason='PREPARING_SOURCE') for s in SYMBOLS}

def blocked(symbol,reason,*,identity=False,raw=False,pool=None,protocol=None):
    return dict(symbol=symbol,state='RAW_ONLY' if identity and raw else 'QUARANTINED',
                identity=identity,raw_capture=raw,price=False,actor=False,qualified=False,
                reason=str(reason),pool=pool,protocol=protocol,at_ms=now_ms())

def freeze(capabilities,at_ms):
    rows={s:dict(capabilities.get(s) or blocked(s,'NO_CAPABILITY_REPORT')) for s in SYMBOLS}
    approved=[s for s in SYMBOLS if rows[s].get('qualified') is True]
    return {'schema':'belial_a1_frozen_cohort_v1','frozen_ms':at_ms,'requested':list(SYMBOLS),
            'qualified_at_start':approved,'sources':rows,'integral_mp12_at_start':len(approved)==12,
            'strategy_rules_changed':False,'late_qualification_observe_only':True}

def source_ready(symbol,cohort,status,at_ms,integrity=True):
    if not integrity:return False,'EVIDENCE_INTEGRITY_BLOCKED'
    if not cohort or symbol not in cohort.get('qualified_at_start',[]):return False,'NOT_IN_FROZEN_QUALIFIED_COHORT'
    if not isinstance(status,dict) or status.get('state')!='ACTIVE':return False,(status or {}).get('reason','SOURCE_NOT_ACTIVE')
    t=status.get('at_ms')
    if type(t) is not int or t>at_ms+500 or at_ms-t>5000:return False,'SOURCE_HEARTBEAT_STALE'
    if status.get('generation')!=0:return False,'RESTARTED_SOURCE_OBSERVE_ONLY'
    if status.get('integrity') is not True:return False,'SOURCE_INTEGRITY_BLOCKED'
    return True,'OK'

def summarize(cohort,statuses,books,at_ms,integrity=True):
    ready_books={(b['venue'],b['symbol']) for b in books if b.get('ready') is True}
    rows=[]
    for s in SYMBOLS:
        source=(cohort or {}).get('sources',{}).get(s,{})
        ready,reason=source_ready(s,cohort,statuses.get(s),at_ms,integrity)
        venues=[v for v in ('hyperliquid','lighter') if (v,s) in ready_books]
        rows.append(dict(source,symbol=s,source_status=statuses.get(s,{}),signal_source_ready=ready,
                         signal_source_reason=reason,venues_ready=venues,
                         paper_prerequisites_ready=ready and bool(venues)))
    return {'requested':len(SYMBOLS),'qualified_at_start':len((cohort or {}).get('qualified_at_start',[])),
            'live_signal_sources':sum(r['signal_source_ready'] for r in rows),'usable_books':len(ready_books),
            'paper_prerequisites_ready':sum(r['paper_prerequisites_ready'] for r in rows),
            'rows':rows,'not_a_signal_or_profit_guarantee':True}
