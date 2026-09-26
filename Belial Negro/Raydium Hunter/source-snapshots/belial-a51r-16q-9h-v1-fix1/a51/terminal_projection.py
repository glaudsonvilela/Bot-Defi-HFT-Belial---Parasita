"""Bounded display projections; never used by the PAPER decision path."""
from __future__ import annotations
import math
CURVE_MAX_POINTS=240
CLOSED_POSITION_HISTORY=60
RECENT_ROWS=20

def envelope(points,maximum=CURVE_MAX_POINTS):
    if not isinstance(points,list):return []
    if len(points)<=maximum:return points
    bucket=max(1,math.ceil((len(points)-2)/max(1,(maximum-2)//2)));out=[points[0]]
    for i in range(1,len(points)-1,bucket):
        rows=points[i:min(i+bucket,len(points)-1)]
        valid=[r for r in rows if isinstance(r,list) and len(r)>=2 and isinstance(r[1],(float,int))]
        if len(valid)!=len(rows):
            if rows:out.append([rows[0][0],None])
            if valid:out.append(valid[-1])
            continue
        lo=min(valid,key=lambda x:x[1]);hi=max(valid,key=lambda x:x[1]);out.extend(sorted([lo,hi] if lo!=hi else [lo],key=lambda x:x[0]))
    out.append(points[-1]);return out

def group_view(group):
    keep=('at_ms','cohort','complete','counts','orders_pending','scenarios','gate','not_strategy_pnl','paper_only','orders','signal_engine_feedback')
    out={k:group[k] for k in keep if k in group};pos=group.get('positions',[])
    active=[r for r in pos if r.get('closed_ms') is None];closed=[r for r in pos if r.get('closed_ms') is not None]
    out['positions']=active+closed[-CLOSED_POSITION_HISTORY:];out['recent']=group.get('recent',[])[-RECENT_ROWS:]
    out['curves']={k:envelope(v) for k,v in group.get('curves',{}).items()}
    out['display']={'curves_downsampled_envelope':True,'curve_max_points':CURVE_MAX_POINTS,'closed_history_limit':CLOSED_POSITION_HISTORY,'source_position_rows':len(pos),'shown_position_rows':len(out['positions']),'not_an_equity_calculator':True}
    return out

def compact_state(state):
    drop=('curves','positions','recent','forward_descriptive','groups','a1_cohort','main','shadow','shadow_focus')
    out={k:v for k,v in state.items() if k not in drop};groups=state.get('groups') or {'CORE12':state}
    out['groups']={g:group_view(v) for g,v in groups.items()}
    for name in ('main','shadow','shadow_focus'):
        value=state.get(name,{}) or {};out[name]={k:value[k] for k in ('counts','policies','schema','paper_only','phase','state','status') if k in value}
    out['terminal_projection']='A51_BOUNDED_DISPLAY_NOT_LEDGER';return out

def compact_agent(agent):
    out={k:v for k,v in agent.items() if k!='models'};heads={}
    for r in agent.get('models',[]):
        k=r['head_id'];h=heads.setdefault(k,dict(primary_n=0,brier_sum=0.,baseline_sum=0.));n=r.get('primary_eval_n',0)
        if n:h['primary_n']+=n;h['brier_sum']+=n*r['primary_brier'];h['baseline_sum']+=n*r['baseline_primary_brier']
    out['head_scores']={k:{'n':v['primary_n'],'brier':v['brier_sum']/v['primary_n'] if v['primary_n'] else None,'baseline_brier':v['baseline_sum']/v['primary_n'] if v['primary_n'] else None} for k,v in heads.items()};return out

def compact_books(books):
    keep=('at_ms','books','transport','venue_runtime','book_state_counts','ingest_messages','messages_per_second','ingest_queue','rust_to_paper_ms_p50','rust_to_paper_ms_p95')
    out={k:books[k] for k in keep if k in books};out['book_errors']=(books.get('book_errors') or [])[-4:];return out

def compact_platform(platform):
    out={k:platform[k] for k in ('schema','at_ms','phase','stage','experiment_label','raw_counts','integrity','paper_stale','failures','orders','coverage_peak','cache') if k in platform}
    cov=platform.get('coverage') or {};rows=[]
    for r in cov.get('rows',[]):
        ss=r.get('source_status') or {};rows.append({k:r.get(k) for k in ('symbol','cohort','identity','raw_capture','price','actor','qualified','reason','state','pool','protocol','signal_source_ready','signal_source_reason','venues_ready','venue_states','paper_prerequisites_ready')}|{'source_status':{k:ss.get(k) for k in ('state','at_ms','reason','actor_ready','generation','integrity')}})
    out['coverage']={k:cov.get(k) for k in ('requested','qualified_at_start','live_signal_sources','usable_books','paper_prerequisites_ready','not_a_signal_or_profit_guarantee')};out['coverage']['rows']=rows
    comps={}
    for name,v in (platform.get('components') or {}).items():
        item={k:v.get(k) for k in ('state','status','at_ms','process_alive','returncode','heartbeat_age_ms','error','reason','component','command','elapsed_seconds','native_pid') if k in v}
        if v.get('last_log'):item['last_log']=str(v['last_log'])[-1800:]
        comps[name]=item
    out['components']=comps;return out

def compact_satellites(value):
    if not isinstance(value,dict):return {}
    out={k:value.get(k) for k in ('schema','at_ms','state','shadow_only','orders','affects_trading','poll_seconds','venues')}
    out['by_symbol']={s:{k:r.get(k) for k in ('state','direction','agreement_count','fresh_count','price_divergence_bps','venues')} for s,r in (value.get('by_symbol') or {}).items()};return out
