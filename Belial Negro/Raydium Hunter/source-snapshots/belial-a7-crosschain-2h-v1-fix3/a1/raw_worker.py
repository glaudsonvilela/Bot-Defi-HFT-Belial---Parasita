"""Archive public logs without normalizing them into marks, labels or orders."""
from __future__ import annotations
import argparse,collections,queue,threading
from a1.common import *
from a1.journal import Journal

def run(session,binary):
    session=Path(session);out=session/'raw';out.mkdir(exist_ok=True);stop=Stop().install();frames=queue.Queue(maxsize=2048);failure=[]
    cfg=session/'spot_prep/RAW_SOURCES.json'
    if not read_json(cfg,{}).get('sources'):heartbeat(out/'status.json','UNAVAILABLE',reason='NO_IDENTITY_VALIDATED_RAW_SOURCE');return 0
    stderr=(out/'native.log').open('w');p=subprocess.Popen([str(binary),str(cfg)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,env=clean_env(),cwd=ROOT,start_new_session=True)
    def reader():
        try:
            for line in iter(p.stdout.readline,b''):
                if len(line)>(8<<20):raise RuntimeError('RAW_ENVELOPE_SIZE')
                frames.put(strict_loads(line),timeout=2)
                if stop.value:break
        except Exception as e:failure.append(str(e))
    thread=threading.Thread(target=reader,daemon=True);thread.start();counts=collections.Counter();journal=Journal(out/'events.jsonl');last=0;transport='STARTING'
    try:
        while not stop.value and not session_expired(session):
            for _ in range(256):
                try:row=frames.get(timeout=.02 if _==0 else 0)
                except queue.Empty:break
                journal.append(row)
                if row.get('symbol'):counts[row['symbol']]+=1
                transport=row.get('transport',transport)
            if failure:raise RuntimeError(failure[0])
            if p.poll() is not None and frames.empty():transport='STOPPED';break
            if now_ms()-last>=500:
                heartbeat(out/'status.json',transport,raw_events=dict(counts),price_validated=False,signal_output=False,queue=frames.qsize());last=now_ms()
    finally:
        stop.value=True
        with contextlib.suppress(Exception):p.stdin.write(b'{"stop":true}\n');p.stdin.flush()
        terminate(p,8);thread.join(2);stderr.close();journal.close()
        heartbeat(out/'status.json','STOPPED',raw_events=dict(counts),price_validated=False,signal_output=False)
        atomic(out/'COMPLETE.json',{'ok':not failure,'counts':dict(counts),'raw_only':True,'signal_output':False,'sha256':digest(out/'events.jsonl')})
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);ap.add_argument('--binary',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session,a.binary))
