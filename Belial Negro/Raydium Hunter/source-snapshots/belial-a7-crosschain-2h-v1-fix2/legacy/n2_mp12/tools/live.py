#!/usr/bin/env python3
from __future__ import annotations
import argparse,collections,fcntl,hashlib,json,os,resource,signal,subprocess,time
from array import array
from pathlib import Path
import common as c
from opencl_core import Core,OpenCLError
import observer as o

STOP=False

def stop_handler(*_):
    global STOP;STOP=True

def gpu_telemetry():
    try:
        cp=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=1.0,check=True)
        line=cp.stdout.strip().splitlines()[0];v=[x.strip() for x in line.split(',')]
        return {'gpu_util_pct':float(v[0]),'vram_used_mib':float(v[1]),'vram_total_mib':float(v[2]),'temperature_c':float(v[3]),'power_w':float(v[4])}
    except Exception as e:return {'error':str(e)}

class TailReader:
    def __init__(self,path:Path,at_end=True):
        c.safe(path);self.path=path;self.f=path.open('rb');st=path.stat();self.identity=(st.st_dev,st.st_ino);self.line=0
        self.f.seek(0,2 if at_end else 0);self.initial_offset=self.f.tell()
    def read(self,limit=2048):
        st=self.path.stat()
        if (st.st_dev,st.st_ino)!=self.identity or st.st_size<self.f.tell():raise RuntimeError('SOURCE_ROTATED_OR_TRUNCATED:'+str(self.path))
        rows=[]
        while len(rows)<limit:
            pos=self.f.tell();b=self.f.readline(131073)
            if not b:break
            if len(b)>131072:raise RuntimeError('SOURCE_LINE_TOO_LONG')
            if not b.endswith(b'\n'):self.f.seek(pos);break
            self.line+=1
            if not b.strip():continue
            r=json.loads(b)
            if not isinstance(r,dict):raise RuntimeError('SOURCE_ROW_NOT_OBJECT')
            rows.append((pos,r))
        return rows
    def warm_swaps(self,now_ms:int):
        end=self.initial_offset;start=max(0,end-8*1024*1024);self.f.seek(start)
        if start:self.f.readline(131073)
        rows=[]
        while self.f.tell()<end:
            b=self.f.readline(131073)
            if not b or not b.endswith(b'\n') or len(b)>131072:break
            try:r=json.loads(b)
            except Exception:continue
            t=int(r.get('received_unix_ms') or 0)
            if now_ms-o.HISTORY_MS<=t<=now_ms:rows.append(r)
            if len(rows)>o.HISTORY_ROWS:rows=rows[-o.HISTORY_ROWS:]
        self.f.seek(end);return rows
    def caught_up(self):return self.f.tell()>=self.path.stat().st_size
    def close(self):self.f.close()

def is_m2_session(p:Path)->bool:
    try:
        if not p.is_dir() or (p/'SESSION_COMPLETE').exists():return False
        # This N2 state is exclusively for T3/MP12. Never attach it to an older
        # M2 directory merely because that directory lacks SESSION_COMPLETE.
        manifest=c.read_json(p/'T3_RUN_MANIFEST.json',{}) or {}
        if manifest.get('schema')!='belial_t3_mp12_session_v1':return False
        if tuple(manifest.get('pools') or ())!=c.POOLS:return False
        s=p/'swaps.jsonl';e=p/'exits.jsonl'
        if not s.is_file() or not e.is_file():return False
        with s.open('rb') as f:
            for _ in range(8):
                b=f.readline(131073)
                if not b:break
                if not b.strip():continue
                r=json.loads(b)
                return r.get('schema')=='belial_m2_swap_v1' and r.get('collector_variant')=='MP12_ULTRA_V1'
        # Empty just-started session is allowed only after the explicit T3 manifest gate above.
        return True
    except Exception:return False

def active_sessions(base:Path):
    ev=base/'evidence';c.safe(ev);out=[]
    if not ev.is_dir():return out
    for p in ev.iterdir():
        try:
            if is_m2_session(p):out.append(p.resolve())
        except Exception:pass
    return sorted(out,key=lambda p:p.stat().st_mtime_ns)

def select_stage(rows,limit_ms:float,min_stage):
    passed=[r for r in rows if float(r['p95_ms'])<=limit_ms]
    if not passed:raise RuntimeError('GPU_BENCHMARK_NO_STAGE_PASSED')
    best=passed[-1]
    if (int(best['dimension']),int(best['heads']))<(int(min_stage[0]),int(min_stage[1])):raise RuntimeError('GPU_BELOW_MINIMUM_LIVE_STAGE')
    return int(best['dimension']),int(best['heads'])

def state_paths(runtime:Path):return runtime/'model.weights.bin',runtime/'model.meta.json'

def save_state(core:Core,model:o.Model,runtime:Path):
    wp,mp=state_paths(runtime);weights=core.read_weights();tmp=wp.with_name(wp.name+'.tmp-'+str(os.getpid()));c.safe(wp);c.safe(tmp)
    with tmp.open('xb') as f:weights.tofile(f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,wp);sha=c.digest(wp)
    meta={'schema':'belial_n2_gpu_state_v1','config_sha256':o.CONFIG_SHA,'dimension':model.dim,'active_heads':model.active_heads,'weight_count':len(weights),'weights_sha256':sha,'saved_ms':c.now_ms(),'stats':{},'last_primary':{}}
    for (s,i),st in model.stats.items():meta['stats'][f'{s}:{i}']=st
    for (s,i),v in model.last_primary.items():meta['last_primary'][f'{s}:{i}']=v
    c.atomic_json(mp,meta);return meta

def load_state(core:Core,model:o.Model,runtime:Path):
    wp,mp=state_paths(runtime);meta=c.read_json(mp)
    if not isinstance(meta,dict) or not wp.is_file():return {'loaded':False,'reason':'NO_COMPATIBLE_STATE'}
    try:
        if meta.get('config_sha256')!=o.CONFIG_SHA or int(meta.get('dimension'))!=model.dim or int(meta.get('active_heads'))!=model.active_heads:return {'loaded':False,'reason':'STATE_CONFIG_OR_STAGE_CHANGED'}
        if c.digest(wp)!=meta.get('weights_sha256'):return {'loaded':False,'reason':'STATE_HASH_MISMATCH'}
        a=array('f');
        with wp.open('rb') as f:a.fromfile(f,int(meta['weight_count']))
        core.write_weights(a)
        for k,st in (meta.get('stats') or {}).items():
            s,i=k.rsplit(':',1);key=(s,int(i))
            if key in model.stats and isinstance(st,dict):model.stats[key].update(st)
        for k,v in (meta.get('last_primary') or {}).items():
            s,i=k.rsplit(':',1);model.last_primary[(s,int(i))]=int(v)
        return {'loaded':True,'saved_ms':meta.get('saved_ms'),'weights_sha256':meta.get('weights_sha256')}
    except Exception as e:return {'loaded':False,'reason':'STATE_LOAD_ERROR:'+str(e)}

class NullJournal:
    def write(self,*_,**__):pass

def follow_session(base:Path,runtime:Path,core:Core,model:o.Model,evidence:Path,telemetry_base:dict):
    run=runtime/'prospective'/(evidence.name+'-segment-'+c.stamp());run.parent.mkdir(parents=True,exist_ok=True);journal=o.Journal(run);model.journal=journal;obs=o.Observer(model,journal,run.name)
    rs=TailReader(evidence/'swaps.jsonl',True);re=TailReader(evidence/'exits.jsonl',True);start=c.now_ms()
    for row in rs.warm_swaps(start):obs.feed_swap(row,start)
    journal.write('lifecycle',{'kind':'SEGMENT_START','at_ms':start,'evidence':str(evidence),'swap_offset':rs.initial_offset,'exit_offset':re.initial_offset,'old_rows_not_forecast':True,'live_weights_seeded_from_replay':False,'gpu':telemetry_base,'dimension':model.dim,'active_heads':model.active_heads,'config_sha256':o.CONFIG_SHA,'signal_engine_feedback':False},True)
    last_status=last_gpu=last_checkpoint=time.monotonic();reason='WATCHER_STOPPED';last_gpu_data={}
    try:
        while not STOP and not (runtime/'STOP_N2').exists():
            now=c.now_ms();batch=[]  # Consume received rows BEFORE maturing deadlines.
            for pos,r in rs.read():batch.append(('swap',int(r.get('received_unix_ms') or now),pos,r))
            for pos,r in re.read():batch.append(('exit',int(r.get('signal_available_unix_ms') or r.get('writer_observed_unix_ms') or now),pos,r))
            batch.sort(key=lambda z:(z[1],0 if z[0]=='swap' else 1,z[2]))
            for kind,_,pos,r in batch:
                seen=c.now_ms()
                if kind=='swap':obs.feed_swap(r,seen)
                else:obs.feed_exit(r,seen,pos)
            caught_up=rs.caught_up() and re.caught_up()
            if caught_up:obs.advance(c.now_ms())
            mono=time.monotonic()
            if mono-last_gpu>=1.0:last_gpu_data=gpu_telemetry();last_gpu=mono
            if mono-last_status>=1.0:
                summary=obs.summary();status={'state':'N2_GPU_SHADOW_ACTIVE','evidence':str(evidence),'run':str(run),'pid':os.getpid(),'gpu':last_gpu_data,'device':core.info,'dimension':model.dim,'active_heads':model.active_heads,'gpu_runtime':summary.get('gpu_runtime'),'counters':summary.get('counters'),'per_pool':summary.get('per_pool'),'pending':summary.get('pending'),'paper_only':True,'approved_for_decisions':False,'signal_engine_feedback':False,'transactions_submitted':False};c.atomic_json(runtime/'status.json',status);c.atomic_json(run/'summary.json',summary);last_status=mono
            if mono-last_checkpoint>=float(o.CONFIG['state']['checkpoint_seconds']):save_state(core,model,runtime);last_checkpoint=mono
            if caught_up and (evidence/'SESSION_COMPLETE').exists():reason='COLLECTOR_SESSION_ENDED';break
            if not batch:time.sleep(float(o.CONFIG['limits']['poll_seconds']))
        summary=obs.finalize(c.now_ms(),reason);c.atomic_json(run/'summary.json',summary);save_state(core,model,runtime);return {'reason':reason,'run':str(run),'summary':summary}
    finally:
        rs.close();re.close();journal.write('lifecycle',{'kind':'SEGMENT_END','at_ms':c.now_ms(),'reason':reason,'signal_engine_feedback':False},True)

def benchmark_and_init(runtime:Path):
    stages=o.CONFIG['gpu_benchmark']['stages'];iters=int(o.CONFIG['gpu_benchmark']['iterations']);batch=int(o.CONFIG['gpu_benchmark']['synthetic_batch']);limit=float(o.CONFIG['gpu_benchmark']['p95_kernel_ms_limit']);minimum=o.CONFIG['gpu_benchmark']['minimum_live_stage']
    core=Core(o.CONFIG.get('preferred_vendor','NVIDIA'));selftest=core.self_test(512);rows=core.benchmark(stages,iters,batch);dim,heads=select_stage(rows,limit,minimum)
    bench={'schema':'belial_n2_gpu_benchmark_v1','device':core.info,'self_test':selftest,'stages':rows,'p95_limit_ms':limit,'selected_dimension':dim,'selected_heads':heads,'minimum_live_stage':minimum,'passed':True,'at_ms':c.now_ms()};c.atomic_json(runtime/'gpu_benchmark.json',bench);return core,bench

def watch(base:Path,runtime:Path,only_evidence:Path|None=None):
    runtime.mkdir(parents=True,exist_ok=True);c.safe(base);c.safe(runtime);resource.setrlimit(resource.RLIMIT_CORE,(0,0));os.nice(int(o.CONFIG['limits']['nice']))
    lock=(runtime/'watch.lock').open('a+');
    try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:print('N2_ALREADY_RUNNING=true');return 0
    (runtime/'STOP_N2').unlink(missing_ok=True);c.atomic_json(runtime/'status.json',{'state':'GPU_INITIALIZING','signal_engine_feedback':False,'transactions_submitted':False})
    core=None
    try:
        core,bench=benchmark_and_init(runtime);model=o.Model(core,int(bench['selected_dimension']),int(bench['selected_heads']),NullJournal());loaded=load_state(core,model,runtime)
        blocked=set();last_gpu=0;g={};c.atomic_json(runtime/'status.json',{'state':'WAITING_FOR_ACTIVE_M2','gpu_benchmark':bench,'memory_state':loaded,'signal_engine_feedback':False,'transactions_submitted':False})
        while not STOP and not (runtime/'STOP_N2').exists():
            found=active_sessions(base)
            if only_evidence is not None:
                want=only_evidence.resolve()
                found=[p for p in found if p.resolve()==want]
            if len(found)>1:c.atomic_json(runtime/'status.json',{'state':'AMBIGUOUS_ACTIVE_M2_SESSIONS','sessions':[str(x) for x in found],'signal_engine_feedback':False});time.sleep(2);continue
            if len(found)==1:
                ev=found[0];key=str(ev)
                if key not in blocked:
                    try:result=follow_session(base,runtime,core,model,ev,{'benchmark':bench,'memory_state':loaded});state={'state':'SESSION_FINISHED','result':result['reason'],'run':result['run'],'gpu_benchmark':bench,'memory_state':loaded,'signal_engine_feedback':False,'transactions_submitted':False}
                    except Exception as e:state={'state':'N2_ERROR_M2_UNTOUCHED','error':str(e),'evidence':str(ev),'signal_engine_feedback':False,'transactions_submitted':False}
                    blocked.add(key);c.atomic_json(runtime/'status.json',state)
            else:
                # Keep final status readable until the runner has archived it.
                if only_evidence is not None and str(only_evidence.resolve()) in blocked:
                    time.sleep(.2);continue
                if time.monotonic()-last_gpu>=2:g=gpu_telemetry();last_gpu=time.monotonic()
                c.atomic_json(runtime/'status.json',{'state':'WAITING_FOR_ACTIVE_M2','gpu':g,'device':core.info,'dimension':model.dim,'active_heads':model.active_heads,'gpu_benchmark':bench,'memory_state':loaded,'signal_engine_feedback':False,'transactions_submitted':False})
            time.sleep(2)
        save_state(core,model,runtime);c.atomic_json(runtime/'status.json',{'state':'STOPPED','signal_engine_feedback':False,'transactions_submitted':False});return 0
    finally:
        if core:core.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base',type=Path,default=c.BASE);ap.add_argument('--runtime',type=Path);ap.add_argument('--only-evidence',type=Path);a=ap.parse_args();base=a.base.resolve();runtime=(a.runtime or base/'runtime'/c.RUNTIME).resolve();only=a.only_evidence.resolve() if a.only_evidence else None;return watch(base,runtime,only)
if __name__=='__main__':
    signal.signal(signal.SIGTERM,stop_handler);signal.signal(signal.SIGINT,stop_handler)
    try:raise SystemExit(main())
    except Exception as e:print('BELIAL_N2_FATAL='+str(e),flush=True);raise
