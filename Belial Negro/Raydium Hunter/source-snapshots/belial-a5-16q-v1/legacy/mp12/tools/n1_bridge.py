#!/usr/bin/env python3
"""Reuse verified N1 algorithm; new M2 qualification memory, no edits to N1.
Only explicit collector PID/evidence are allowed, never automatic discovery.
"""
import argparse,json,os,sys,time
from pathlib import Path
import common as c

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True)
    ap.add_argument('--pid',type=int,required=True);a=ap.parse_args()
    release=Path(__file__).resolve().parent.parent
    install=json.loads((release/'INSTALLED_SHA256.json').read_text());c.verify(release,install)
    n1=c.BASE/'releases/r5d-n1-numeric-v1'
    inv=json.loads((n1/'INSTALLED_SHA256.json').read_text());c.verify(n1,inv)
    expected=json.loads((release/'EXPECTED_N1.json').read_text());c.verify(n1,expected)
    evidence=a.evidence.resolve(strict=True);evidence.relative_to(c.BASE/'evidence');c.safe(evidence)
    exe=Path(f'/proc/{a.pid}/exe').resolve(strict=True)
    if exe!=(release/'bin/belial-r5c2-live-profiler').resolve():raise RuntimeError('WRONG_COLLECTOR_PROCESS')
    args=Path(f'/proc/{a.pid}/cmdline').read_bytes().split(b'\0')
    i=args.index(b'--evidence')
    if Path(os.fsdecode(args[i+1])).resolve()!=evidence:raise RuntimeError('WRONG_SESSION')
    sys.path.insert(0,str(n1/'src'))
    import live,observer
    # Isolation from existing N1 prospective memory and all development weights.
    rt=evidence/'n1_qualification';rt.mkdir(exist_ok=False)
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024));os.nice(10)
    result=live.follow_session(c.BASE,rt,n1/'bin/belial-n1',evidence,a.pid)
    c.new_json(rt/'RESULT.json',{'mode':'NEW_LIVE_QUALIFICATION_NOT_APPROVED_OOS','result':result,
      'learner_sources_modified':False,'existing_n1_memory_modified':False,'replay_seeded':False,
      'signal_engine_feedback':False,'qualification_model_promoted':False})
    print('M2_N1_BRIDGE_OK=true',flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:print('M2_N1_BRIDGE_FAIL='+str(e),flush=True);raise
