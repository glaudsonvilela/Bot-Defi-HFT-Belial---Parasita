#!/usr/bin/env python3
from __future__ import annotations
import collections,dataclasses,hashlib,heapq,json,math,os,time
from pathlib import Path
from typing import Any
import common as c
from features import Mark,swap_to_mark,build_base,short_bps,BASE_NAMES

ROOT=Path(__file__).resolve().parents[1]
CONFIG=json.loads((ROOT/'CONFIG_N2.json').read_text())
HEADS=CONFIG['heads'];MAX_GAP=int(CONFIG['quality']['max_observation_gap_ms']);MAX_SIGNAL_AGE=int(CONFIG['quality']['max_signal_age_ms'])
MAX_PENDING=int(CONFIG['limits']['max_pending']);HISTORY_MS=int(CONFIG['limits']['history_window_ms']);HISTORY_ROWS=int(CONFIG['limits']['history_rows_per_pool'])
UNIQUE_HORIZONS=sorted({int(h['horizon_ms']) for h in HEADS})
CONFIG_SHA=hashlib.sha256(c.canonical(CONFIG).encode()).hexdigest()

def clipped_logloss(p,y):
    p=max(1e-12,min(1-1e-12,float(p)));return -(math.log(p) if y else math.log(1-p))

@dataclasses.dataclass
class Sample:
    ident:str;symbol:str;start:int;entry_price:float;entry_source_ms:int;base:list[float];pred:list[float];baseline:list[float];primary:list[bool]
    source_event_id:str;source_exit_offset:int;remaining:set[int]

class Journal:
    def __init__(self,run:Path):
        self.run=run;run.mkdir(parents=True,exist_ok=False)
        self.paths={n:run/(n+'.jsonl') for n in ('predictions','labels','training','anomalies','lifecycle')}
    def write(self,name,obj,fsync=False):c.append_jsonl(self.paths[name],obj,fsync)

class Model:
    def __init__(self,core,active_dim:int,active_heads:int,journal:Journal):
        self.core=core;self.dim=int(active_dim);self.active_heads=int(active_heads);self.journal=journal
        self.stats={(s,i):{'updates':0,'positives':0,'eval_n':0,'brier_sum':0.0,'log_sum':0.0,'primary_eval_n':0,'primary_brier_sum':0.0,'primary_log_sum':0.0,'baseline_brier_sum':0.0,'baseline_log_sum':0.0} for s in c.POOLS for i in range(self.active_heads)}
        self.last_primary={}
        self.predict_ms=collections.deque(maxlen=2048);self.train_ms=collections.deque(maxlen=2048)
    def predict(self,symbol:str,start:int,base:list[float])->tuple[list[float],list[float],list[bool]]:
        pool=c.POOL_INDEX[symbol];t0=time.perf_counter_ns();p=self.core.predict_batch(pool,[base],self.dim,self.active_heads)[0];self.predict_ms.append((time.perf_counter_ns()-t0)/1e6)
        bas=[];prim=[]
        for i in range(self.active_heads):
            st=self.stats[(symbol,i)];bas.append((st['positives']+1.0)/(st['updates']+2.0))
            h=int(HEADS[i]['horizon_ms']);key=(symbol,i);last=self.last_primary.get(key)
            is_primary=last is None or start-last>=h
            if is_primary:self.last_primary[key]=start
            prim.append(is_primary)
        return p,bas,prim
    def label(self,sample:Sample,head_idx:int,y:int,valid:bool,reason:str|None,at:int,mfe:float|None,mae:float|None,max_gap:int|None,obs:int)->dict:
        h=HEADS[head_idx];p=float(sample.pred[head_idx]);b=float(sample.baseline[head_idx]);primary=bool(sample.primary[head_idx]);trained=False;gpu_prob_before_update=None
        if valid:
            st=self.stats[(sample.symbol,head_idx)];st['eval_n']+=1;st['brier_sum']+=(p-y)**2;st['log_sum']+=clipped_logloss(p,y)
            if primary:
                st['primary_eval_n']+=1;st['primary_brier_sum']+=(p-y)**2;st['primary_log_sum']+=clipped_logloss(p,y);st['baseline_brier_sum']+=(b-y)**2;st['baseline_log_sum']+=clipped_logloss(b,y)
                intent={'schema':'belial_n2_train_intent_v1','id':sample.ident,'symbol':sample.symbol,'head':head_idx,'head_id':h['id'],'label':int(y),'label_available_ms':at,'prediction_ms':sample.start,'base_frozen':sample.base,'dimension':self.dim,'config_sha256':CONFIG_SHA}
                self.journal.write('training',intent,True)
                lr=float(CONFIG['learning']['learning_rate'])/math.sqrt(1.0+st['updates']/2000.0);l2=float(CONFIG['learning']['l2'])
                t0=time.perf_counter_ns();gpu_prob_before_update=self.core.train(c.POOL_INDEX[sample.symbol],head_idx,sample.base,self.dim,int(y),lr,l2);self.train_ms.append((time.perf_counter_ns()-t0)/1e6)
                st['updates']+=1;st['positives']+=int(y);trained=True
        return {'schema':'belial_n2_delayed_label_a2_v1','label_revision':CONFIG['label_revision'],'target_is_observed_path_not_continuous':True,'id':sample.ident,'symbol':sample.symbol,'head':head_idx,'head_id':h['id'],'direction':h['direction'],'threshold_bps':h['threshold_bps'],'horizon_ms':h['horizon_ms'],'prediction_ms':sample.start,'label_available_ms':at,'valid':bool(valid),'reason':reason,'label':int(y) if valid else None,'primary_nonoverlap':primary,'trained':trained,'p_issued':p,'baseline_issued':b,'gpu_prob_before_update':gpu_prob_before_update,'observed_mfe_bps':mfe,'observed_mae_bps':mae,'observations':obs,'max_gap_ms':max_gap,'executable_net_pnl':None,'prediction_is_not_trade':True,'signal_engine_feedback':False}
    def summary(self):
        rows=[]
        for s in c.POOLS:
            for i in range(self.active_heads):
                st=self.stats[(s,i)];n=st['eval_n'];pn=st['primary_eval_n'];u=st['updates']
                rows.append({'symbol':s,'head':i,'head_id':HEADS[i]['id'],'updates':u,'positives':st['positives'],'eval_n':n,'brier':st['brier_sum']/n if n else None,'log_loss':st['log_sum']/n if n else None,'primary_eval_n':pn,'primary_brier':st['primary_brier_sum']/pn if pn else None,'primary_log_loss':st['primary_log_sum']/pn if pn else None,'baseline_primary_brier':st['baseline_brier_sum']/pn if pn else None,'baseline_primary_log_loss':st['baseline_log_sum']/pn if pn else None})
        return rows
    def runtime_metrics(self):
        return {'predict_p50_ms':c.percentile(list(self.predict_ms),.5),'predict_p95_ms':c.percentile(list(self.predict_ms),.95),'predict_max_ms':max(self.predict_ms) if self.predict_ms else None,'train_p50_ms':c.percentile(list(self.train_ms),.5),'train_p95_ms':c.percentile(list(self.train_ms),.95),'train_max_ms':max(self.train_ms) if self.train_ms else None}

class Observer:
    def __init__(self,model:Model,journal:Journal,session:str):
        self.model=model;self.journal=journal;self.session=session;self.history={s:collections.deque() for s in c.POOLS};self.samples={};self.tasks=[];self.seq=0;self.clock=0;self.counts=collections.Counter();self.pool_counts={s:collections.Counter() for s in c.POOLS};self.label_history={s:collections.deque() for s in c.POOLS};self.sample_slots={}
    def stat(self,s,k,n=1):self.counts[k]+=n;self.pool_counts[s][k]+=n
    def _trim(self,s,now):
        q=self.history[s]
        while q and q[0].t<now-HISTORY_MS:q.popleft()
        while len(q)>HISTORY_ROWS:q.popleft();self.stat(s,'history_capacity_drop')
    def feed_swap(self,row:dict,available_ms:int):
        s=str(row.get('symbol') or '')
        if s not in c.POOLS:return
        m=swap_to_mark(row)
        if not m:self.stat(s,'invalid_swap');return
        if self.history[s] and m.t<self.history[s][-1].t:self.stat(s,'out_of_order_swap');return
        self.history[s].append(m);self._trim(s,max(available_ms,m.t));self.stat(s,'swaps')
        t=int(row.get('a2_available_ms') or row.get('a1_available_ms') or available_ms)
        self._append_label(s,t,m.price,'SWAP',available_ms)
    def _append_label(self,s,t,price,source,available_ms):
        if t<=0 or t>available_ms or not math.isfinite(price) or price<=0:
            self.stat(s,'invalid_label_observation');return False
        q=self.label_history[s]
        if q and t<q[-1][0]:
            self.stat(s,'out_of_order_label_observation');return False
        q.append((t,float(price),source))
        while q and q[0][0]<available_ms-HISTORY_MS:q.popleft()
        while len(q)>HISTORY_ROWS:q.popleft();self.stat(s,'label_history_capacity_drop')
        return True
    def feed_price_sample(self,row,available_ms):
        s=row.get('symbol')
        if s not in c.POOLS:return False
        if row.get('valid') is not True:
            self.stat(s,'unavailable_price_sample');return False
        if row.get('not_a_swap') is not True or row.get('quote')!='WSOL':
            self.stat(s,'invalid_price_sample_contract');return False
        slot=row.get('slot');t=row.get('available_ms');price=row.get('price')
        if type(slot) is not int or slot<=self.sample_slots.get(s,0) or type(t) is not int:
            self.stat(s,'replayed_price_sample');return False
        try:accepted=self._append_label(s,t,float(price),'POOL_STATE_SNAPSHOT',available_ms)
        except (ValueError,TypeError):accepted=False
        if accepted:self.sample_slots[s]=slot;self.stat(s,'verified_price_samples')
        return accepted
    def feed_exit(self,row:dict,available_ms:int,offset:int=0):
        s=str(row.get('symbol') or '')
        if s not in c.POOLS:return
        try:
            if 'BACKFILL' in str(row.get('source','')).upper() or row.get('paper_entry_eligible') is False:raise ValueError('BACKFILL_EXCLUDED')
            ev=int(row.get('event_unix_ms') or 0);avail=int(row.get('signal_available_unix_ms') or row.get('writer_observed_unix_ms') or ev)
            if ev<=0 or avail<ev:raise ValueError('BAD_SIGNAL_TIME')
            age=max(0,available_ms-ev)
            if age>MAX_SIGNAL_AGE:self.stat(s,'stale_exit');return
            if len(self.samples)>=MAX_PENDING:self.stat(s,'pending_capacity');return
            hist=list(self.history[s]);base,dbg=build_base(row,hist,available_ms)
            ident=hashlib.sha256(f'{CONFIG_SHA}|{self.session}|{s}|{row.get("event_id")}|{available_ms}'.encode()).hexdigest()
            pred,baseline,primary=self.model.predict(s,available_ms,base)
            sample=Sample(ident,s,available_ms,float(dbg['reference_price']),int(dbg['reference_source_ms']),base,pred,baseline,primary,str(row.get('event_id') or ''),offset,set(UNIQUE_HORIZONS))
            self.samples[ident]=sample
            for h in UNIQUE_HORIZONS:self.seq+=1;heapq.heappush(self.tasks,(available_ms+h,self.seq,ident,h))
            self.stat(s,'predictions')
            self.journal.write('predictions',{'schema':'belial_n2_gpu_forecast_v1','kind':'N2_GPU_FORECAST_NOT_TRADE','id':ident,'session':self.session,'symbol':s,'pool_role':CONFIG['pool_roles'][s],'published_ms':available_ms,'source_event_id':sample.source_event_id,'source_exit_offset':offset,'dimension':self.model.dim,'active_heads':self.model.active_heads,'base_features':dict(zip(BASE_NAMES,base)),'reference_price':sample.entry_price,'reference_source_ms':sample.entry_source_ms,'reference_age_ms':dbg['reference_age_ms'],'signal_age_ms':dbg['signal_age_ms'],'probabilities':{HEADS[i]['id']:pred[i] for i in range(self.model.active_heads)},'baselines':{HEADS[i]['id']:baseline[i] for i in range(self.model.active_heads)},'primary_nonoverlap':{HEADS[i]['id']:primary[i] for i in range(self.model.active_heads)},'prediction_is_not_trade':True,'approved_for_decisions':False,'executable_net_pnl':None,'signal_engine_feedback':False,'transactions_submitted':False})
        except (ValueError,TypeError,OverflowError) as e:
            self.stat(s,'invalid_exit');self.journal.write('anomalies',{'kind':'EXIT_SKIPPED','symbol':s,'at_ms':available_ms,'reason':str(e),'offset':offset})
    def _label_window(self,sample:Sample,horizon:int):
        end=sample.start+horizon;marks=[m for m in self.label_history[sample.symbol] if sample.start<m[0]<=end]
        if len(marks)<2:return False,'FEWER_THAN_TWO_POST_START_OBSERVATIONS',None,None,len(marks),None
        times=[sample.start]+[m[0] for m in marks]+[end];gap=max(times[i+1]-times[i] for i in range(len(times)-1))
        if gap>MAX_GAP:return False,'OBSERVATION_GAP_GT_30S',None,None,len(marks),gap
        mfe=max(short_bps(sample.entry_price,m[1]) for m in marks);mae=max(-short_bps(sample.entry_price,m[1]) for m in marks)
        return True,None,mfe,mae,len(marks),gap
    def advance(self,at:int):
        if at<self.clock:raise RuntimeError('CLOCK_REGRESSION')
        self.clock=at
        while self.tasks and self.tasks[0][0]<=at:
            _,_,ident,horizon=heapq.heappop(self.tasks);sample=self.samples.get(ident)
            if sample is None or horizon not in sample.remaining:continue
            valid,reason,mfe,mae,obs,gap=self._label_window(sample,horizon)
            for i,h in enumerate(HEADS[:self.model.active_heads]):
                if int(h['horizon_ms'])!=horizon:continue
                if valid:y=int((mfe if h['direction']=='favorable' else mae)>=float(h['threshold_bps']))
                else:y=0
                row=self.model.label(sample,i,y,valid,reason,at,mfe,mae,gap,obs);self.journal.write('labels',row,fsync=bool(valid and sample.primary[i]))
                self.stat(sample.symbol,'valid_labels' if valid else 'censored_labels')
                if valid:self.stat(sample.symbol,'positive_labels' if y else 'negative_labels')
                if row['trained']:self.stat(sample.symbol,'training_updates')
            sample.remaining.discard(horizon)
            if not sample.remaining:self.samples.pop(ident,None)
    def finalize(self,at:int,reason='COLLECTOR_SESSION_ENDED'):
        self.advance(at)
        for sample in list(self.samples.values()):
            for i,h in enumerate(HEADS[:self.model.active_heads]):
                if int(h['horizon_ms']) not in sample.remaining:continue
                self.journal.write('labels',{'schema':'belial_n2_delayed_label_a2_v1','label_revision':CONFIG['label_revision'],'target_is_observed_path_not_continuous':True,'id':sample.ident,'symbol':sample.symbol,'head':i,'head_id':h['id'],'prediction_ms':sample.start,'label_available_ms':at,'valid':False,'reason':'HORIZON_INCOMPLETE_'+reason,'label':None,'trained':False,'prediction_is_not_trade':True,'signal_engine_feedback':False})
                self.stat(sample.symbol,'censored_labels')
        self.samples.clear();self.tasks.clear();return self.summary()
    def summary(self):
        return {'schema':'belial_n2_gpu_summary_a2_v1','label_revision':CONFIG['label_revision'],'session':self.session,'clock_ms':self.clock,'dimension':self.model.dim,'active_heads':self.model.active_heads,'counters':dict(self.counts),'per_pool':{s:dict(v) for s,v in self.pool_counts.items()},'models':self.model.summary(),'gpu_runtime':self.model.runtime_metrics(),'pending':len(self.samples),'paper_only':True,'prediction_is_not_trade':True,'approved_for_decisions':False,'signal_engine_feedback':False,'transactions_submitted':False}
