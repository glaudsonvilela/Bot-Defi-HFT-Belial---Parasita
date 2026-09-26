"""Forecast publication is timed after inference; duplicate source events are not reissued."""
from __future__ import annotations
import hashlib,heapq,math
from a3.common import now_ms,ROOT,sys
sys.path.insert(0,str(ROOT/'n2_a2/tools'))
import observer as o
from features import build_base,BASE_NAMES

class CausalObserver(o.Observer):
    def __init__(self,model,journal,session,clock=now_ms,deadline=None):
        super().__init__(model,journal,session);self.clock_now=clock;self.deadline=deadline;self.issued=set()
    def feed_exit(self,row,available_ms,offset=0):
        s=str(row.get('symbol') or '')
        if s not in o.c.POOLS:return
        try:
            if 'BACKFILL' in str(row.get('source','')).upper() or row.get('paper_entry_eligible') is False:raise ValueError('BACKFILL_EXCLUDED')
            event=int(row.get('event_unix_ms') or 0)
            avail=int(row.get('signal_available_unix_ms') or row.get('writer_observed_unix_ms') or event)
            observed=max(available_ms,self.clock_now())
            if event<=0 or avail<event or avail>observed:raise ValueError('BAD_SIGNAL_TIME')
            if observed-event>o.MAX_SIGNAL_AGE:self.stat(s,'stale_exit');return
            source_id=str(row.get('event_id') or 'offset:'+str(offset))
            key=(s,source_id)
            if key in self.issued:self.stat(s,'duplicate_exit_ignored');return
            if len(self.samples)>=o.MAX_PENDING:self.stat(s,'pending_capacity');return
            base,dbg=build_base(row,list(self.history[s]),observed)
            p,b,primary=self.model.predict(s,observed,base)
            if len(p)!=self.model.active_heads or any(not math.isfinite(float(x)) or not 0<=float(x)<=1 for x in p):raise RuntimeError('GPU_PREDICTION_INVALID')
            issued=max(observed,self.clock_now())
            if self.deadline is not None and issued>=self.deadline:self.stat(s,'inference_past_deadline');return
            ident=hashlib.sha256(f'{o.CONFIG_SHA}|{self.session}|{s}|{source_id}|{issued}'.encode()).hexdigest()
            sample=o.Sample(ident,s,issued,float(dbg['reference_price']),int(dbg['reference_source_ms']),base,p,b,primary,source_id,offset,set(o.UNIQUE_HORIZONS))
            # Persist before admitting to labels; a failed journal can never look like a forecast.
            self.journal.write('predictions',{'schema':'belial_n2_a3_prospective_forecast_v1','id':ident,
                'kind':'N2_GPU_FORECAST_NOT_TRADE','session':self.session,'symbol':s,'pool_role':o.CONFIG['pool_roles'][s],
                'published_ms':issued,'inference_started_ms':observed,'features_frozen_ms':observed,
                'source_event_id':source_id,'source_exit_offset':offset,'dimension':self.model.dim,'active_heads':self.model.active_heads,
                'base_features':dict(zip(BASE_NAMES,base)),'reference_price':sample.entry_price,
                'reference_source_ms':sample.entry_source_ms,'reference_age_ms':issued-sample.entry_source_ms,
                'signal_age_ms':issued-event,'probabilities':{o.HEADS[i]['id']:p[i] for i in range(self.model.active_heads)},
                'baselines':{o.HEADS[i]['id']:b[i] for i in range(self.model.active_heads)},
                'primary_nonoverlap':{o.HEADS[i]['id']:primary[i] for i in range(self.model.active_heads)},
                'prediction_is_not_trade':True,'approved_for_decisions':False,'signal_engine_feedback':False,'transactions_submitted':False},True)
            self.issued.add(key);self.samples[ident]=sample
            for i,v in enumerate(primary):
                if v:self.model.last_primary[(s,i)]=issued
            for h in o.UNIQUE_HORIZONS:self.seq+=1;heapq.heappush(self.tasks,(issued+h,self.seq,ident,h))
            self.stat(s,'predictions')
        except (ValueError,TypeError,OverflowError) as exc:
            self.stat(s,'invalid_exit');self.journal.write('anomalies',{'kind':'EXIT_SKIPPED','symbol':s,'at_ms':available_ms,'reason':str(exc),'offset':offset})
