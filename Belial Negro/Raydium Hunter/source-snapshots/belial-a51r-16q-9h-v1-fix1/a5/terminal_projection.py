"""Bounded presentation snapshots. Trading journals and decision objects are untouched."""
from __future__ import annotations
import math

def envelope(points,maximum=480):
    if not isinstance(points,list):return []
    if len(points)<=maximum:return points
    # First/last + extrema of each bucket. For DISPLAY only, never equity/risk math.
    bucket=max(1,math.ceil((len(points)-2)/max(1,(maximum-2)//2)))
    out=[points[0]]
    for i in range(1,len(points)-1,bucket):
        rows=points[i:min(i+bucket,len(points)-1)]
        valid=[r for r in rows if isinstance(r,list) and len(r)>=2 and isinstance(r[1],(float,int))]
        if len(valid)!=len(rows):
            # Preserve a discontinuity even in a mixed bucket, conservatively.
            if rows:out.append([rows[0][0],None])
            if valid:out.append(valid[-1])
            continue
        lo=min(valid,key=lambda x:x[1]);hi=max(valid,key=lambda x:x[1]);out.extend(sorted([lo,hi] if lo!=hi else [lo],key=lambda x:x[0]))
    out.append(points[-1]);return out

def group_view(group):
    keep=('at_ms','cohort','complete','counts','orders_pending','scenarios','gate','not_strategy_pnl','paper_only','orders','signal_engine_feedback')
    out={k:group[k] for k in keep if k in group}
    pos=group.get('positions',[]);active=[r for r in pos if r.get('closed_ms') is None];closed=[r for r in pos if r.get('closed_ms') is not None]
    out['positions']=active+closed[-100:];out['recent']=group.get('recent',[])[-24:]
    out['curves']={k:envelope(v) for k,v in group.get('curves',{}).items()}
    out['display']={'curves_downsampled_envelope':True,'history_limit':100,'source_position_rows':len(pos),
                    'shown_position_rows':len(out['positions']),'source_curve_points':sum(len(x) for x in group.get('curves',{}).values()),
                    'not_an_equity_calculator':True}
    return out

def compact_state(state):
    # Top-level CORE copies were needlessly serializing the same positions and curves twice.
    copy={k:v for k,v in state.items() if k not in ('curves','positions','recent','forward_descriptive','groups','a1_cohort','main','shadow','shadow_focus')}
    groups=state.get('groups') or {'CORE12':state}
    copy['groups']={g:group_view(v) for g,v in groups.items()}
    for name in ('main','shadow','shadow_focus'):
        value=state.get(name,{}) or {};copy[name]={k:value[k] for k in ('counts','policies','schema','paper_only','phase','state','status') if k in value}
    copy['terminal_projection']='A4_BOUNDED_DISPLAY_NOT_LEDGER';return copy

def compact_agent(agent):
    out={k:v for k,v in agent.items() if k!='models'}
    heads={}
    for r in agent.get('models',[]):
        key=r['head_id'];h=heads.setdefault(key,dict(head_id=key,primary_n=0,brier_sum=0.,baseline_sum=0.))
        n=r.get('primary_eval_n',0)
        if n:h['primary_n']+=n;h['brier_sum']+=n*r['primary_brier'];h['baseline_sum']+=n*r['baseline_primary_brier']
    out['head_scores']={k:{'n':v['primary_n'],'brier':v['brier_sum']/v['primary_n'] if v['primary_n'] else None,
                           'baseline_brier':v['baseline_sum']/v['primary_n'] if v['primary_n'] else None} for k,v in heads.items()}
    return out
