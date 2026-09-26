"""Isolated unchanged OpenCL model. A1 feed has an explicit cohort, not a fake MP12 approval."""
from __future__ import annotations
import argparse,shutil
from a1.common import *
add_legacy('n2_mp12')
import live,observer as observer

def seed_existing(runtime):
    # Exact previous runtime directory. Old files are never written or migrated in place.
    old=BASE/'runtime'/live.c.RUNTIME
    src=old/'model.weights.bin';meta=old/'model.meta.json';data=read_json(meta,{}) or {}
    if not src.is_file() or data.get('config_sha256')!=observer.CONFIG_SHA:return {'copied':False,'reason':'NO_COMPATIBLE_PRIOR_STATE'}
    before=digest(src)
    if before!=data.get('weights_sha256'):return {'copied':False,'reason':'PRIOR_HASH_MISMATCH'}
    shutil.copyfile(src,runtime/'model.weights.bin');atomic(runtime/'model.meta.json',data)
    if digest(src)!=before or digest(runtime/'model.weights.bin')!=before:
        (runtime/'model.weights.bin').unlink();(runtime/'model.meta.json').unlink();return {'copied':False,'reason':'SOURCE_CHANGED'}
    return {'copied':True,'source':str(old),'sha256':before,'old_runtime_modified':False}

def run(session):
    session=Path(session);runtime=session/'n2';runtime.mkdir(exist_ok=True);stop=Stop().install();core=None;journal=None;rs=re=None
    heartbeat(runtime/'status.json','GPU_INITIALIZING',approved_for_decisions=False)
    try:
        core,bench=live.benchmark_and_init(runtime)
        if (bench['selected_dimension'],bench['selected_heads'])!=(8192,32):raise RuntimeError('N2_8192_X_32_REQUIRED')
        model=observer.Model(core,8192,32,live.NullJournal());seed=seed_existing(runtime);loaded=live.load_state(core,model,runtime)
        heartbeat(runtime/'status.json','GPU_READY_WAITING_COHORT',dimension=8192,active_heads=32,memory_state=loaded,seed=seed,approved_for_decisions=False)
        while not stop.value and not session_expired(session) and session_state(session).get('phase')!='CAPTURING':time.sleep(.1)
        if stop.value or session_expired(session):return 0
        ev=session/'aggregate';run=runtime/'prospective';journal=observer.Journal(run);model.journal=journal;obs=observer.Observer(model,journal,session.name)
        rs=live.TailReader(ev/'swaps.jsonl',False);re=live.TailReader(ev/'exits.jsonl',False)
        journal.write('lifecycle',{'kind':'A1_COHORT_START','at_ms':now_ms(),'cohort':read_json(session/'COHORT.json'),'old_rows_forecast':False,'signal_engine_feedback':False},True)
        last=last_save=time.monotonic();deadline_ms=session_state(session)['deadline_ms']
        while not stop.value and now_ms()<deadline_ms:
            batch=[]
            for offset,row in rs.read():batch.append((int(row.get('a1_available_ms',0)),0,offset,row))
            for offset,row in re.read():batch.append((int(row.get('a1_available_ms',0)),1,offset,row))
            batch.sort(key=lambda r:r[:3])
            for _,kind,offset,row in batch:
                if now_ms()>=deadline_ms:break
                if kind==0:obs.feed_swap(row,now_ms())
                else:obs.feed_exit(row,now_ms(),offset)
            caught=rs.caught_up() and re.caught_up()
            if caught:obs.advance(min(now_ms(),deadline_ms))
            if time.monotonic()-last>=1:
                summary=obs.summary();atomic(run/'summary.json',summary)
                heartbeat(runtime/'status.json','N2_GPU_SHADOW_ACTIVE',dimension=8192,active_heads=32,gpu=live.gpu_telemetry(),device=core.info,
                          gpu_runtime=summary.get('gpu_runtime'),counters=summary.get('counters'),per_pool=summary.get('per_pool'),pending=summary.get('pending'),approved_for_decisions=False,signal_engine_feedback=False);last=time.monotonic()
            if time.monotonic()-last_save>=float(observer.CONFIG['state']['checkpoint_seconds']):live.save_state(core,model,runtime);last_save=time.monotonic()
            if caught and ((ev/'SESSION_COMPLETE').exists() or session_expired(session)):break
            if not batch:time.sleep(float(observer.CONFIG['limits']['poll_seconds']))
        summary=obs.finalize(min(now_ms(),deadline_ms),'A1_CAPTURE_ENDED_INCOMPLETE_WINDOWS_CENSORED');atomic(run/'summary.json',summary);live.save_state(core,model,runtime)
        heartbeat(runtime/'status.json','SESSION_FINISHED',dimension=8192,active_heads=32,counters=summary.get('counters'),per_pool=summary.get('per_pool'),approved_for_decisions=False)
        atomic(runtime/'COMPLETE.json',{'ok':True,'summary':summary,'old_weights_modified':False})
    except Exception as e:
        heartbeat(runtime/'status.json','N2_UNAVAILABLE_OTHER_SERVICES_CONTINUE',error=str(e),approved_for_decisions=False)
        atomic(runtime/'COMPLETE.json',{'ok':False,'error':str(e),'other_services_untouched':True})
    finally:
        for reader in (rs,re):
            if reader:reader.close()
        if journal:
            # The original journal owns independently flushed files.
            for f in getattr(journal,'files',{}).values():
                with contextlib.suppress(Exception):f.flush();f.close()
        if core and hasattr(core,'close'):core.close()
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))
