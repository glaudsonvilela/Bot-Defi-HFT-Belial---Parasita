"""Offline replay only. Never attaches to a live runtime and never trains N2."""
from __future__ import annotations
import argparse,hashlib
from pathlib import Path
from a3.common import *
from a3.journal import read_verified,Journal
from a3.paper_controller import Controller

def replay(path,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    cfg=read_json(Path(path).parent/'CONFIG_EFFECTIVE.json')
    if not isinstance(cfg,dict):raise ValueError('REPLAY_CONFIG_REQUIRED')
    sink=Journal(out/'outputs.jsonl');control=Controller(cfg,lambda k,r:sink.append({'kind':k,'body':r}))
    count=0
    try:
        for e in read_verified(path):control.apply(e);count+=1
        state=control.snapshot(control.last_at)
        atomic(out/'STATE.json',state);sink.close()
        summary={'ok':True,'verified_inputs':count,'output_sha256':digest(out/'outputs.jsonl'),
                 'source_input_sha256':digest(path),'live_runtime_touched':False,'n2_training':False,
                 'ledger_replay_not_new_performance_sample':True}
        atomic(out/'REPLAY.json',summary);return summary
    finally:sink.close()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--journal',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    print(canonical(replay(a.journal,a.out)))
if __name__=='__main__':main()
