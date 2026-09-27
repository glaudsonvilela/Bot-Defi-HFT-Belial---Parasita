"""Recompute every output event from the sealed, availability-time ordered inputs."""
import hashlib,itertools,json
from pathlib import Path
from a51.common import canonical,atomic
from a51.journal import read_verified
from a8.lab import Lab

def replay(session,save=True):
    session=Path(session);universe=json.loads((session/'selection/UNIVERSE.json').read_text())['selected']
    expected=iter(read_verified(session/'decisions.jsonl'));count=0
    def compare(event):
        nonlocal count
        ref=next(expected,None)
        if ref is None or canonical(ref)!=canonical(event):raise ValueError('REPLAY_EVENT_MISMATCH_'+str(count))
        count+=1
    lab=Lab(universe,compare);inputs=0
    for event in read_verified(session/'inputs.jsonl'):
        lab.ingest(event['row'],event['at_ms']);inputs+=1
    if next(expected,None) is not None:raise ValueError('REPLAY_MISSING_OUTPUTS')
    state=lab.view(lab.last_ms);expected_state=json.loads((session/'FINAL_STATE.json').read_text())
    if canonical(state)!=canonical(expected_state):raise ValueError('REPLAY_FINAL_STATE_MISMATCH')
    result=dict(ok=True,inputs=inputs,decisions=count,final_state_sha256=hashlib.sha256(canonical(state).encode()).hexdigest(),orders=False)
    if save:atomic(session/'REPLAY.json',result,True)
    return result
if __name__=='__main__':
    import sys
    print(json.dumps(replay(sys.argv[1])))
