#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,zipfile,time
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter')
RELEASE='r5d-m2-live-v1'

def stamp():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
def now():return time.time_ns()//1_000_000

def safe(p:Path):
    for q in (p,*p.parents):
        if q.is_symlink():raise RuntimeError('SYMLINK_REFUSED:'+str(q))

def digest(p):
    safe(p);h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def new_json(p,obj):
    safe(p)
    with p.open('x',encoding='utf-8') as f:
        json.dump(obj,f,sort_keys=True,indent=2,allow_nan=False,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())

def atomic(p,obj):
    safe(p);tmp=p.with_name(p.name+'.tmp-'+str(os.getpid()));new_json(tmp,obj);os.replace(tmp,p)

def verify(root,inv,exact=False,extras=()):
    safe(root)
    if exact:
        actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
        if actual!=set(inv)|set(extras):raise RuntimeError('MANIFEST_MEMBER_MISMATCH:'+str(root))
    for rel,sha in inv.items():
        rp=Path(rel)
        if rp.is_absolute() or '..' in rp.parts:raise ValueError('MANIFEST_PATH')
        p=root/rp;safe(p)
        if not p.is_file() or digest(p)!=sha:raise RuntimeError('HASH_MISMATCH:'+str(p))

def zip_new(root,out):
    safe(root);safe(out)
    inv={}
    for p in sorted(root.rglob('*')):
        safe(p)
        if p.is_file():inv[str(p.relative_to(root))]=digest(p)
    with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for rel,h in inv.items():
            p=root/rel;raw=p.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=h:raise RuntimeError('SOURCE_CHANGED_WHILE_PACKAGING')
            z.writestr(root.name+'/'+rel,raw)
        z.writestr(root.name+'/ZIP_MEMBER_SHA256.json',json.dumps(inv,sort_keys=True,indent=2))
    with zipfile.ZipFile(out) as z:
        if z.testzip():raise RuntimeError('ZIP_CRC')
    return digest(out)

class Journal:
    def __init__(self,root):
        safe(root);root.mkdir(parents=True,exist_ok=False);self.root=root;self.files={};self.last=time.monotonic();self.rows=0
    def emit(self,kind,obj):
        if kind not in ('events','trades','telemetry'):raise ValueError('JOURNAL_KIND')
        if kind not in self.files:self.files[kind]=(self.root/(kind+'.jsonl')).open('x',encoding='utf-8')
        f=self.files[kind];f.write(json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n');f.flush();self.rows+=1
        if time.monotonic()-self.last>=1:self.sync()
    def sync(self):
        for f in self.files.values():f.flush();os.fsync(f.fileno())
        self.last=time.monotonic()
    def close(self):
        self.sync()
        for f in self.files.values():f.close()

class Reader:
    def __init__(self,p):
        safe(p);self.p=p;self.f=p.open('rb');st=p.stat();self.id=(st.st_dev,st.st_ino);self.rows=0
    def read(self,limit=512):
        st=self.p.stat()
        if self.id!=(st.st_dev,st.st_ino) or st.st_size<self.f.tell():raise RuntimeError('SOURCE_ROTATED_OR_TRUNCATED')
        out=[]
        for _ in range(limit):
            offset=self.f.tell();b=self.f.readline(65537)
            if not b:break
            if len(b)>65536:raise RuntimeError('SOURCE_LINE_TOO_LONG')
            if not b.endswith(b'\n'):self.f.seek(offset);break
            if not b.strip():continue
            row=json.loads(b)
            if not isinstance(row,dict):raise RuntimeError('ROW_NOT_OBJECT')
            self.rows+=1;out.append(row)
        return out
    def close(self):self.f.close()
