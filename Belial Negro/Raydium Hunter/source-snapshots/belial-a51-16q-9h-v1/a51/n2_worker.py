"""N2 lifecycle A4. Predictions are evidence, not trading permissions.
Deferred inputs + causal timestamping + prequential scoring + immutable compatible weights.
No hidden restart of model/labels after corruption or a GPU exception.
"""
from __future__ import annotations
import argparse, collections, threading
from a51.common import *
from a51.agent_io import DeferredTail
from a4 import model_store
sys.path.insert(0,str(ROOT/'n2_a2/tools'))
import live, observer
from a51.causal_observer import CausalObserver

class AgentJournal(observer.Journal):
    def __init__(self,run):
        super().__init__(run);self.latest={};self.horizons={};self.reasons=collections.Counter()
        self.prediction_ms=None;self.training_ms=None;self.label_ms=None;self.labelled_ids=set()
    def write(self,name,row,fsync=False):
        super().write(name,row,fsync or name=='predictions')
        if name=='predictions':
            self.prediction_ms=row['published_ms'];self.latest[row['symbol']]=row
        elif name=='labels':
            self.label_ms=row['label_available_ms']
            h=next((x for x in observer.HEADS if x['id']==row.get('head_id')),None)
            horizon=str(h['horizon_ms']//1000) if h else 'unknown'
            c=self.horizons.setdefault(horizon,dict(valid=0,censored=0,positive=0,negative=0,updates=0))
            c['valid' if row.get('valid') else 'censored']+=1
            if row.get('valid'):c['positive' if row.get('label') else 'negative']+=1
            else:self.reasons[row.get('reason','UNKNOWN')]+=1
            if row.get('trained'):c['updates']+=1;self.training_ms=row['label_available_ms']
    def view(self,model):
        rows=model.summary();scored=[r for r in rows if r['primary_eval_n']]
        n=sum(r['primary_eval_n'] for r in scored)
        b=sum(r['primary_eval_n']*r['primary_brier'] for r in scored)/n if n else None
        base=sum(r['primary_eval_n']*r['baseline_primary_brier'] for r in scored)/n if n else None
        return {'last_prediction_ms':self.prediction_ms,'last_training_ms':self.training_ms,'last_label_ms':self.label_ms,
                'latest_predictions':self.latest,'horizons':self.horizons,'censor_reasons':dict(self.reasons),
                'evaluation':{'method':'PREQUENTIAL_NONOVERLAP_WITHIN_SYMBOL_HEAD','primary_scored_head_labels':n,
                    'brier':b,'baseline_brier':base,'brier_skill':(1-b/base) if base and b is not None else None,
                    'heads_are_correlated':True,'not_independent_trade_count':True,'lower_brier_is_better':True,
                    'trading_improvement_proven':False},'models':rows}

class Pump:
    def __init__(self,session,obs):
        self.session=Path(session);self.obs=obs
        self.readers={'swap':DeferredTail(self.session/'aggregate/swaps.jsonl'),
                      'exit':DeferredTail(self.session/'aggregate/exits.jsonl'),
                      'sample':DeferredTail(self.session/'price_samples/observations.jsonl')}
        self.exits_seen=0;self.future_rows=0;self.late_rows=0;self.loops=0;self.waiting=[]
    def step(self,at,deadline,forecast=True):
        self.loops+=1;self.waiting=[k for k,r in self.readers.items() if not r.available()]
        if self.waiting:return 0
        batch=[]
        for order,(kind,reader) in enumerate(self.readers.items()):
            for offset,row in reader.read():
                available=int(row.get('a2_available_ms') or row.get('a1_available_ms') or row.get('available_ms') or 0)
                batch.append((available,order,offset,kind,row))
        batch.sort(key=lambda x:x[:3])
        for available,_,offset,kind,row in batch:
            if available>deadline:self.late_rows+=1;continue
            if available>at:self.future_rows+=1;continue
            if kind=='swap':self.obs.feed_swap(row,at)
            elif kind=='sample':self.obs.feed_price_sample(row,at)
            else:
                self.exits_seen+=1
                if forecast and at<deadline:self.obs.feed_exit(row,at,offset)
        # Never train an already-due label before draining its available observations.
        if all(r.caught_up() for r in self.readers.values()):self.obs.advance(min(at,deadline))
        return len(batch)
    def view(self):
        return {'missing_channels':self.waiting,'readers':{k:r.view() for k,r in self.readers.items()},
                'eligible_exit_rows_seen':self.exits_seen,'future_rows_rejected':self.future_rows,
                'post_deadline_rows_rejected':self.late_rows,'loops':self.loops}
    def close(self):
        for r in self.readers.values():r.close()

def run(session,*,factory=None,store=None):
    session=Path(session);runtime=session/'n2';runtime.mkdir(exist_ok=True);stop=Stop().install()
    core=None;pump=None;journal=None;obs=None;loaded={};saved={};failure=None;completed=False
    heartbeat(runtime/'status.json','GPU_INITIALIZING',approved_for_decisions=False,signal_engine_feedback=False)
    try:
        core,bench=(factory or live.benchmark_and_init)(runtime)
        if (bench['selected_dimension'],bench['selected_heads'])!=(8192,32):raise RuntimeError('N2_8192_X_32_REQUIRED')
        model=observer.Model(core,8192,32,live.NullJournal())
        mode=os.environ.get('BELIAL_A5_MODEL_MODE','continue')
        loaded=model_store.restore(core,model,runtime,live,store,session_state(session).get('created_ms',now_ms()),mode)
        last=0
        while not stop.value and not session_expired(session) and session_state(session).get('phase')!='CAPTURING':
            if now_ms()-last>=1000:
                heartbeat(runtime/'status.json','GPU_READY_WAITING_COHORT',dimension=8192,active_heads=32,
                          memory_state=loaded,counters={},approved_for_decisions=False,signal_engine_feedback=False);last=now_ms()
            time.sleep(.1)
        if stop.value or session_expired(session):raise RuntimeError('SESSION_ENDED_BEFORE_N2_CAPTURE')
        journal=AgentJournal(runtime/'prospective');model.journal=journal
        deadline=session_state(session)['deadline_ms']
        obs=CausalObserver(model,journal,session.name,deadline=deadline);pump=Pump(session,obs)
        last=last_save=last_gpu=0;gpu={}
        journal.write('lifecycle',{'kind':'A4_AGENT_START','at_ms':now_ms(),'mode':mode,'memory_state':loaded,
                      'label_revision':observer.CONFIG['label_revision'],'signal_engine_feedback':False,
                      'old_rows_not_retroactive_forecasts':True},True)
        while not stop.value and now_ms()<deadline and session_state(session).get('phase')=='CAPTURING':
            at=now_ms();n=pump.step(at,deadline)
            if at-last_gpu>=10000:gpu=live.gpu_telemetry();last_gpu=at
            if at-last>=1000:
                summary=obs.summary();evidence=journal.view(model)
                status='WAITING_INPUT_CHANNELS' if pump.waiting else 'ACTIVE_NO_ELIGIBLE_FORECAST_YET' if not journal.prediction_ms else 'N2_ACTIVE_SHADOW'
                atomic(runtime/'prospective/summary.json',summary)
                atomic(runtime/'AGENT_VIEW.json',dict(evidence,at_ms=at,contract=model_store.contract(),session=session.name))
                heartbeat(runtime/'status.json',status,dimension=8192,active_heads=32,device=core.info,gpu=gpu,
                          memory_state=loaded,counters=summary['counters'],per_pool=summary['per_pool'],
                          pending=summary['pending'],gpu_runtime=summary['gpu_runtime'],
                          inputs=pump.view(),last_prediction_ms=journal.prediction_ms,last_training_ms=journal.training_ms,
                          evaluation=evidence['evaluation'],horizons=evidence['horizons'],
                          approved_for_decisions=False,signal_engine_feedback=False,orders=False)
                last=at
            if at-last_save>=float(observer.CONFIG['state']['checkpoint_seconds'])*1000:
                live.save_state(core,model,runtime);last_save=at
            if not n:time.sleep(.02)
        # Bounded drain supplies already-available marks to pending labels; no new predictions.
        end=min(now_ms(),deadline)
        for _ in range(64):
            if not pump.step(end,deadline,False):break
        summary=obs.finalize(end,'A4_ENDED_INCOMPLETE_WINDOWS_CENSORED')
        atomic(runtime/'prospective/summary.json',summary);live.save_state(core,model,runtime)
        completed=not stop.value and now_ms()>=deadline
        counts=summary['counters']
        if completed and counts.get('training_updates',0)>0:
            saved=model_store.publish(runtime,session,store)
        qualification={'worker_completed':completed,'forecasts_written':counts.get('predictions',0),
            'valid_labels':counts.get('valid_labels',0),'updates':counts.get('training_updates',0),
            'prospective_pipeline_demonstrated':bool(completed and counts.get('predictions',0) and counts.get('valid_labels',0) and counts.get('training_updates',0)),
            'trading_advantage_demonstrated':False,'reason':'OBSERVE_COUNTERS_NOT_GPU_BENCHMARK',
            'long_horizons_valid':{h:v['valid'] for h,v in journal.horizons.items() if h!='unknown' and int(h)>=300}}
        atomic(runtime/'AGENT_VALIDATION.json',qualification,True)
        atomic(runtime/'AGENT_VIEW.json',dict(journal.view(model),at_ms=now_ms(),qualification=qualification,memory_state=loaded))
        heartbeat(runtime/'status.json','SESSION_FINISHED' if completed else 'INTERRUPTED',dimension=8192,active_heads=32,
                  counters=counts,per_pool=summary['per_pool'],memory_state=loaded,published_checkpoint=saved,
                  qualification=qualification,horizons=journal.horizons,approved_for_decisions=False)
        atomic(runtime/'COMPLETE.json',{'ok':completed,'summary':summary,'qualification':qualification,'checkpoint':saved,'error':None if completed else 'INTERRUPTED'},True)
    except Exception as exc:
        failure=str(exc)
        if journal:journal.write('lifecycle',{'kind':'A5_AGENT_FAILED','error':failure,'at_ms':now_ms()},True)
        heartbeat(runtime/'status.json','FAILED_N2_OTHER_SERVICES_CONTINUE',error=failure,
                  counters=dict(obs.counts) if obs else {},approved_for_decisions=False,signal_engine_feedback=False)
        atomic(runtime/'COMPLETE.json',{'ok':False,'error':failure,'other_services_untouched':True},True)
    finally:
        if pump:pump.close()
        if core:
            with contextlib.suppress(Exception):core.close()
    return 0 if completed and failure is None else 2

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session))
