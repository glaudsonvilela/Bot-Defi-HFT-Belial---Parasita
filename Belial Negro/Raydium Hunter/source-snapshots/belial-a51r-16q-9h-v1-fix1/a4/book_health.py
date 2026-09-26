"""Display-only venue/book health semantics.

Execution freshness remains the inherited Book.ready(max_age=2000) gate.  This
module only prevents a stale instrument from being mislabeled as a dead venue.
"""
from __future__ import annotations

DISCONNECTED={'DISCONNECTED','ACCESS_DENIED','UNAVAILABLE','STOPPED','BUILD_UNAVAILABLE'}
RECONNECTING={'CONNECTING','RECONNECTING','COOLDOWN'}

def classify_book(row,transport,elapsed_ms=0,max_age_ms=2000):
    row=dict(row or {})
    raw_age=row.get('age_ms')
    age=(float(raw_age)+max(0,float(elapsed_ms))) if isinstance(raw_age,(int,float)) else None
    t=str(transport or 'UNKNOWN')
    if t in DISCONNECTED:state='DISCONNECTED'
    elif t in RECONNECTING:state='RECONNECTING'
    elif row.get('status')=='BOOK_STALE' or age is None or age>max_age_ms:state='STALE'
    elif row.get('ready') is True:state='FRESH'
    else:state='INVALID'
    return {'display_state':state,'display_transport':t,'effective_age_ms':age,
            'ready_for_fills':bool(row.get('ready') is True and state=='FRESH')}
