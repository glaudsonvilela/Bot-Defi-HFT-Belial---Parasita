import json,pathlib,collections
p=pathlib.Path('forensic_a7_fix3/m2-a7-20260927T013647917050Z');u=json.loads((p/'A7_SUMMARY.json').read_text())['universe'];symbols=[c['symbol'] for c in u]
hist={src:{s:collections.deque(maxlen=400) for s in symbols} for src in ('mini','flow','book')};counts={src:collections.Counter() for src in hist};ticks=0
for l in (p/'inputs.jsonl').open():
 e=json.loads(l)['event'];r=e['row'];now=e['at_ms'];kind=r['kind'];s=None;src=None;t=0
 if kind=='wide' and isinstance(r['data'],dict) and r['data'].get('e')=='24hrMiniTicker':s=r['data']['s'];t=r['data']['E'];src='mini'
 elif kind=='flow':s=r['symbol'];t=r['event_ms'];src='flow'
 elif kind=='depth':s=r['data']['s'];t=r['data']['E'];src='book'
 if src and s in hist[src] and -500<=now-t<= (10000 if src=='mini' else 2500 if src=='flow' else 2000) and 0<=now-r.get('received_ms',now)<=2000:
  h=hist[src][s]
  if not h or t>h[-1]:
   if h and t//2000==h[-1]//2000:h[-1]=t
   else:h.append(t)
 if kind=='tick':
  ticks+=1
  for src in hist:
   for s,h in hist[src].items():
    valid=bool(h and now-h[-1]<=10000)
    if valid:
     for age in (30000,120000):
      ref=next((t for t in reversed(h) if t<=now-age),None)
      if ref is None or now-age-ref>10000:valid=False;break
    recent=[t for t in h if now-t<=60000];continuity=[t for t in h if now-t<=130000]
    if valid and len(recent)>=11 and max([b-a for a,b in zip(continuity,continuity[1:])] or [0])<=15000:counts[src][s]+=1
result=dict(ticks=ticks,total_checks=ticks*len(symbols),cadence_ready=dict(counts),ready_percent={src:100*sum(c.values())/(ticks*len(symbols)) for src,c in counts.items()},note='OBSERVATION_TIMESTAMPS_ONLY_NOT_RETURNS_OR_PROFIT; historic flow has no executed price',orders=False)
pathlib.Path('a8_investigation/CADENCE.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
