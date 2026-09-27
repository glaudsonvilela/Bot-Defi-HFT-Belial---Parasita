"""Exploratory replay of observed books; no missing aggTrade prices are reconstructed."""
import json,pathlib,sys,collections,time
sys.path.insert(0,str(pathlib.Path('a8_release').resolve()))
from a8.lab import Lab
from a51.journal import read_verified
from a51.common import canonical
p=pathlib.Path('forensic_a7_fix3/m2-a7-20260927T013647917050Z');out=pathlib.Path('a8_investigation');universe=json.loads((p/'selection/UNIVERSE.json').read_text())['selected'];events=[];lab=Lab(universe,lambda e:events.append(json.loads(canonical(e))));begin=time.monotonic();n=0
for e in read_verified(p/'inputs.jsonl'):
 lab.ingest(e['row'],e['at_ms']);n+=1
 if n%100000==0:print('A8_REPLAY_PROGRESS='+str(n),file=sys.stderr,flush=True)
state=lab.view(lab.last_ms);financial={'SIGNAL','ENTRY_FILLED','ENTRY_CANCELLED','ENTRY_EXPIRED','EXIT_REQUEST','EXIT_FILLED','OPEN_CENSORED'}
old=[e for e in read_verified(p/'decisions.jsonl') if e['arm']=='CONTROLE' and e['kind'] in financial]
new=[e for e in events if e['arm']=='CONTROLE' and e['kind'] in financial]
# Diagnostic rejection labels changed; trading events and position accounting must not.
matched=canonical(old)==canonical(new)
result=dict(orders=False,inputs=n,elapsed_seconds=time.monotonic()-begin,control_financial_events_identical=matched,control_events=len(old),limitations=['IN_SAMPLE_EXPLORATION_NOT_OUT_OF_SAMPLE_VALIDATION','HISTORIC_FLOW_HAS_NO_PRICE_PRECO_ARM_NOT_EVALUABLE','BNB_FAILED_IN_ORIGINAL_RECORDING','NO_DATA_AFTER_ORIGINAL_TWO_HOUR_END'],arms={})
for name,a in state['arms'].items():
 result['arms'][name]=dict(price_source=a['price_source'],counts=a['counts'],rejections=a['rejections'],realized_net_ex_funding=a['realized_net_ex_funding'],open_net_estimate_ex_funding=sum(p['pnl_live_net_ex_funding'] for p in a['positions']) if all(p['pnl_live_net_ex_funding'] is not None for p in a['positions']) else None,open=len(a['positions']),closed=len(a['closed']))
(out/'PROJECTED_REPLAY.json').write_text(json.dumps(result,indent=2));(out/'PROJECTED_STATE.json').write_text(json.dumps(state));(out/'PROJECTED_DECISIONS.jsonl').write_text(''.join(canonical(e)+'\n' for e in events));print(json.dumps(result))
if not matched:raise SystemExit('CONTROL_FINANCIAL_EVENTS_CHANGED')
