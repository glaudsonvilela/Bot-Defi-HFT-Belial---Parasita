"""Small local primitives. No account credentials or transaction submission."""
from __future__ import annotations
import contextlib, hashlib, json, os, signal, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LEGACY=ROOT/'legacy'
BASE=Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter')
CORE12=('FARTCOIN', 'BOME', 'WIF', 'POPCAT', 'RAY', 'JUP', 'BONK', 'PENGU', 'PUMP', 'USELESS', 'GRASS', 'PYTH')
FOCUS4=('RENDER', 'KMNO', 'MEW', 'MPLX')
SYMBOLS=CORE12+FOCUS4
def cohort_of(symbol):
    if symbol not in SYMBOLS: raise ValueError("UNKNOWN_SYMBOL")
    return "CORE12" if symbol in CORE12 else "FOCUS4"
MINUTES=90
DURATION_MS=5_400_000
VERSION='BELIAL_A5_16Q_V1'
def now_ms(): return time.time_ns()//1_000_000
def canonical(obj): return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def strict_loads(raw): return json.loads(raw,parse_constant=lambda v: (_ for _ in ()).throw(ValueError('NONFINITE_JSON:'+v)))
def read_json(path, default=None):
    try: return strict_loads(Path(path).read_text())
    except (OSError, ValueError): return default

def safe(path):
    p=Path(path)
    for q in (p,*p.parents):
        if q.is_symlink(): raise RuntimeError('SYMLINK_REFUSED:'+str(q))
    return p

def atomic(path,obj,durable=False):
    p=safe(path);p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_name(p.name+'.tmp-'+str(os.getpid()))
    data=canonical(obj)+'\n'
    with temp.open('w',encoding='utf-8') as f:
        f.write(data);f.flush()
        if durable:os.fsync(f.fileno())
    os.replace(temp,p)
    if durable:
        fd=os.open(p.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

def digest(path):
    h=hashlib.sha256()
    with safe(path).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def heartbeat(path,state,**kw):
    atomic(path,dict(state=state,at_ms=now_ms(),pid=os.getpid(),version=VERSION,**kw))

def add_legacy(*folders):
    # Called by isolated processes before importing modules with historic names.
    for folder in reversed(folders):sys.path.insert(0,str(LEGACY/folder/'tools'))

class JsonTail:
    """A complete-line reader; no truncation/rotation and no partial line commit."""
    def __init__(self,path,limit_bytes=4<<20):
        self.path=Path(path);self.f=None;self.identity=None;self.max_line=limit_bytes
    def read(self,limit=256):
        if self.f is None:
            if not self.path.exists():return []
            safe(self.path);self.f=self.path.open('rb');st=self.path.stat();self.identity=(st.st_dev,st.st_ino)
        st=self.path.stat()
        if self.identity!=(st.st_dev,st.st_ino) or st.st_size<self.f.tell():raise RuntimeError('SOURCE_ROTATED_OR_TRUNCATED:'+str(self.path))
        out=[]
        for _ in range(limit):
            offset=self.f.tell();raw=self.f.readline(self.max_line+1)
            if not raw:break
            if len(raw)>self.max_line:raise RuntimeError('SOURCE_LINE_TOO_LARGE')
            if not raw.endswith(b'\n'):self.f.seek(offset);break
            if not raw.strip():continue
            value=strict_loads(raw)
            if not isinstance(value,dict):raise RuntimeError('SOURCE_EVENT_NOT_OBJECT')
            out.append((offset,value))
        return out
    def close(self):
        if self.f:self.f.close()

class Stop:
    def __init__(self):self.value=False
    def handler(self,*_):self.value=True
    def install(self):
        signal.signal(signal.SIGTERM,self.handler);signal.signal(signal.SIGINT,self.handler);return self

def command(module,*args):return [sys.executable,'-B','-m','a5.'+module,*map(str,args)]
def clean_env():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
    env['PYTHONPATH']=str(ROOT)
    env.pop('BELIAL_T3_POOL_MANIFEST',None);env.pop('BELIAL_A1_SENSOR',None)
    return env

def spawn(args,log,env=None):
    p=safe(log);p.parent.mkdir(parents=True,exist_ok=True)
    f=p.open('ab',buffering=0)
    try:return subprocess.Popen(args,stdout=f,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,env=env or clean_env(),cwd=ROOT,start_new_session=True),f
    except BaseException:f.close();raise

def terminate(proc,timeout=5):
    if proc is None or proc.poll() is not None:return
    with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGKILL)
        proc.wait(timeout=5)

def session_state(path):return read_json(Path(path)/'SESSION.json',{}) or {}
def session_expired(session):
    state=session_state(session)
    if state.get('phase') in ('STOPPING','FINISHED','FAILED'):return True
    deadline=state.get('deadline_monotonic_ns')
    return isinstance(deadline,int) and time.monotonic_ns()>=deadline
