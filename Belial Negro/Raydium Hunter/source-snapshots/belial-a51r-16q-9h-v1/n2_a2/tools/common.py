#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,time
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter')
RELEASE='a2-n2-24-sampled-v1'
RUNTIME='a2-n2-24-sampled'
POOLS=('FARTCOIN', 'BOME', 'WIF', 'POPCAT', 'RAY', 'JUP', 'BONK', 'PENGU', 'PUMP', 'USELESS', 'GRASS', 'PYTH', 'JTO', 'RENDER', 'W', 'TNSR', 'DRIFT', 'TRUMP', 'MELANIA', 'ORCA', 'HNT', 'MEW', 'MPLX', 'KMNO')
POOL_INDEX={s:i for i,s in enumerate(POOLS)}

def stamp(): return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
def now_ms(): return time.time_ns()//1_000_000

def safe(p:Path):
    p=Path(p)
    for q in (p,*p.parents):
        if q.is_symlink(): raise RuntimeError('SYMLINK_REFUSED:'+str(q))

def digest(p:Path):
    safe(p);h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()

def canonical(obj): return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)

def atomic_json(p:Path,obj):
    safe(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.tmp-'+str(os.getpid()))
    if tmp.exists(): tmp.unlink()
    with tmp.open('x',encoding='utf-8') as f:
        json.dump(obj,f,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)

def append_jsonl(p:Path,obj,fsync=False):
    safe(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a',encoding='utf-8') as f:
        f.write(canonical(obj)+'\n');f.flush()
        if fsync: os.fsync(f.fileno())

def read_json(p:Path,default=None):
    try:
        safe(p);x=json.loads(p.read_text());return x
    except (OSError,ValueError,TypeError):return default

def percentile(xs,q):
    if not xs:return None
    a=sorted(float(x) for x in xs);q=max(0.0,min(1.0,float(q)))
    if len(a)==1:return a[0]
    k=(len(a)-1)*q;lo=int(k);hi=min(len(a)-1,lo+1);t=k-lo
    return a[lo]*(1-t)+a[hi]*t
