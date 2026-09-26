"""Synthetic IPC fixture, NEVER a public exchange connector."""
import argparse,json,sys,time,signal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from a1.common import atomic,now_ms,ROOT,heartbeat
sys.path.insert(0,str(ROOT/'legacy/perp/tests'))
from test_books_paper import meta,hl,li
p=argparse.ArgumentParser();p.add_argument('--venue');p.add_argument('--session',type=Path);a,_=p.parse_known_args();n=0;stop=False

def finish(*_):
 global stop;stop=True
signal.signal(signal.SIGTERM,finish)
def emit(k,b):
 global n;n+=1;print(json.dumps(dict(source=a.venue,epoch=0,sequence=n,kind=k,body=b)),flush=True)
heartbeat(a.session/'venues'/a.venue/'status.json','FIXTURE_ONLY',not_market=True)
emit('markets',dict(venue=a.venue,markets=[meta(a.venue)]));i=0
while not stop:
 i+=1;t=now_ms();frame=hl(t) if a.venue=='hyperliquid' else li(t,nonce=i,begin=i-1,snapshot=i==1)
 if a.venue=='lighter' and i==20:frame=li(t,nonce=i+2,begin=i+1,snapshot=False)
 emit('book',frame);time.sleep(.012)
