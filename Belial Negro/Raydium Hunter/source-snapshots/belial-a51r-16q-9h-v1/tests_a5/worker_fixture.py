"""Local production-worker test with a synthetic backend and a 13-second window. Not a GPU/market test."""
import json,sys,time,threading
from pathlib import Path
from helpers import FakeCore,swap,ex,append
from a5.common import atomic,now_ms
from a4 import n2_worker
p=Path(sys.argv[1]);s=p/'m2-a4-synthetic-delayed';s.mkdir();(s/'aggregate').mkdir();(s/'price_samples').mkdir()
start=now_ms();deadline=start+13000
atomic(s/'SESSION.json',{'phase':'CAPTURING','created_ms':start-1000,'started_ms':start,'deadline_ms':deadline,'deadline_monotonic_ns':time.monotonic_ns()+13_000_000_000})
# Reproduce the missing channel after capture has already started, not just missing at import.
for name in ('swaps','exits'):(s/'aggregate'/(name+'.jsonl')).touch()
def producer():
    time.sleep(.4);(s/'price_samples/observations.jsonl').touch();t=now_ms()
    for k in range(10):append(s/'aggregate/swaps.jsonl',swap(t-9000+k*900,1.,str(k)))
    append(s/'aggregate/exits.jsonl',ex(t))
    while now_ms()<deadline-100:
        time.sleep(.35);append(s/'aggregate/swaps.jsonl',swap(now_ms(),.99,str(now_ms())))
t=threading.Thread(target=producer);t.start()
n2_worker.live.gpu_telemetry=lambda:{'backend':'SYNTHETIC_NOT_GPU'}
result=n2_worker.run(s,factory=lambda r:(FakeCore(),{'selected_dimension':8192,'selected_heads':32}),store=p/'models')
t.join();raise SystemExit(result)
