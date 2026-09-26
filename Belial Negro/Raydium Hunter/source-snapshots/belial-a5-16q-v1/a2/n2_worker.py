"""Isolated A2 label revision; frozen cohorts, unchanged features/heads, fresh weights."""
from __future__ import annotations
import argparse,shutil
from a2.common import *
sys.path.insert(0,str(ROOT/'n2_a2/tools'))
import live,observer as observer

def seed_existing(runtime):
    # Labels have a new declared sampling contract; old weights are preserved,
    # not reinterpreted or overwritten. Independent per-asset weights remain.
    return {'copied':False,'reason':'A2_NEW_LABEL_REVISION_FRESH_MODEL_OLD_WEIGHTS_PRESERVED'}

def run(session):
    session=Path(session);runtime=session/'n2';runtime.mkdir(exist_ok=True);stop=Stop().install();core=None;journal=None;rs=re=rp=None
    heartbeat(runtime/'status.json','GPU_INITIALIZING',approved_for_decisions=False)
    try:
        core,bench=live.benchmark_and_init(runtime)
        if (bench['selected_dimension'],bench['selected_heads'])!=(8192,32):raise RuntimeError('N2_8192_X_32_REQUIRED')
        model=observer.Model(core,8192,32,live.NullJournal());seed=seed_existing(runtime);loaded=live.load_state(core,model,runtime)
        heartbeat(runtime/'status.json','GPU_READY_WAITING_COHORT',dimension=8192,active_heads=32,memory_state=loaded,seed=seed,approved_for_decisions=False)
        while not stop.value and not session_expired(session) and session_state(session).get('phase')!='CAPTURING':time.sleep(.1)
        if stop.value or session_expired(session):return 0
        ev=session/'aggregate';run=runtime/'prospective';journal=observer.Journal(run);model.journal=journal;obs=observer.Observer(model,journal,session.name)
        rs=live.TailReader(ev/'swaps.jsonl',False);re=live.TailReader(ev/'exits.jsonl',False);rp=live.TailReader(session/'price_samples/observations.jsonl',False)
        journal.write('lifecycle',{'kind':'A2_COHORT_START','at_ms':now_ms(),'cohort':read_json(session/'COHORT.json'),'old_rows_forecast':False,'signal_engine_feedback':False},True)
        last=last_save=time.monotonic();deadline_ms=session_state(session)['deadline_ms']
        while not stop.value and now_ms()<deadline_ms:
            batch=[]
            for offset,row in rs.read():batch.append((int(row.get('a1_available_ms',0)),0,offset,row))
            for offset,row in re.read():batch.append((int(row.get('a1_available_ms',0)),1,offset,row))
            for offset,row in rp.read():batch.append((int(row.get('available_ms',0)),2,offset,row))
            batch.sort(key=lambda r:r[:3])
            for _,kind,offset,row in batch:
                if now_ms()>=deadline_ms:break
                if kind==0:obs.feed_swap(row,now_ms())
                elif kind==1:obs.feed_exit(row,now_ms(),offset)
                else:obs.feed_price_sample(row,now_ms())
            caught=rs.caught_up() and re.caught_up() and rp.caught_up()
            if caught:obs.advance(min(now_ms(),deadline_ms))
            if time.monotonic()-last>=1:
                summary=obs.summary();atomic(run/'summary.json',summary)
                heartbeat(runtime/'status.json','N2_GPU_SHADOW_ACTIVE',dimension=8192,active_heads=32,gpu=live.gpu_telemetry(),device=core.info,
                          gpu_runtime=summary.get('gpu_runtime'),counters=summary.get('counters'),per_pool=summary.get('per_pool'),pending=summary.get('pending'),approved_for_decisions=False,signal_engine_feedback=False);last=time.monotonic()
            if time.monotonic()-last_save>=float(observer.CONFIG['state']['checkpoint_seconds']):live.save_state(core,model,runtime);last_save=time.monotonic()
            if caught and ((ev/'SESSION_COMPLETE').exists() or session_expired(session)):break
            if not batch:time.sleep(float(observer.CONFIG['limits']['poll_seconds']))
        summary=obs.finalize(min(now_ms(),deadline_ms),'A2_CAPTURE_ENDED_INCOMPLETE_WINDOWS_CENSORED');atomic(run/'summary.json',summary);live.save_state(core,model,runtime)
        heartbeat(runtime/'status.json','SESSION_FINISHED',dimension=8192,active_heads=32,counters=summary.get('counters'),per_pool=summary.get('per_pool'),approved_for_decisions=False)
        atomic(runtime/'COMPLETE.json',{'ok':True,'summary':summary,'old_weights_modified':False})
    except Exception as e:
        heartbeat(runtime/'status.json','N2_UNAVAILABLE_OTHER_SERVICES_CONTINUE',error=str(e),approved_for_decisions=False)
        atomic(runtime/'COMPLETE.json',{'ok':False,'error':str(e),'other_services_untouched':True})
    finally:
        for reader in (rs,re,rp):
            if reader:reader.close()
        if journal:
            # The original journal owns independently flushed files.
            for f in getattr(journal,'files',{}).values():
                with contextlib.suppress(Exception):f.flush();f.close()
        if core and hasattr(core,'close'):core.close()
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))
