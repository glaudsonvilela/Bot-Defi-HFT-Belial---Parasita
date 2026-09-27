"""Recoverable per-event faults. Hard native/IO failures remain local quarantines.
Recovering a feed is a NEW actor epoch; old inventory and queued signals are not reused.
"""
from __future__ import annotations
import argparse
from a3.common import *
WARMUP_MS=60_000
class Monitor:
    def __init__(self):
        self.previous=None;self.last_fault=0;self.gap_epoch=0;self.recoveries=0;self.was_ready=False;self.ready_since=0
        self.hard=None;self.reason='WAITING_NATIVE_SOURCE';self.last_native_gap=0
    def update(self,d,h,at):
        counters=tuple(int(d.get(k,0)) for k in ('rpc_errors','dropped_events','decode_errors'))
        generation=int(h.get('generation',0));signature=(counters,generation)
        fault=None
        if self.previous is not None:
            old,old_gen=self.previous
            if any(a<b for a,b in zip(counters,old)) or generation<old_gen:self.hard='NATIVE_COUNTER_OR_EPOCH_REGRESSION'
            elif counters!=old:fault='EVENT_UNRESOLVED_CONTEXT_RESET'
            elif generation!=old_gen:fault='WS_RECONNECTED_NEW_EPOCH'
        elif any(counters):fault='PRIOR_UNRESOLVED_EVENT'
        self.previous=signature
        ng=int(h.get('actor_gap_ms',0))
        if ng>self.last_native_gap:fault='NATIVE_ACTOR_CONTEXT_RESET';self.last_native_gap=ng
        if h.get('state')=='DISCONNECTED':fault='WS_DISCONNECTED'
        native_ok=bool(d) and 0<=at-int(d.get('updated_unix_ms',0))<=5000
        transport_ok=h.get('state')=='ACTIVE' and 0<=at-int(h.get('at_ms',0))<=45_000
        if d and not native_ok:fault='NATIVE_HEARTBEAT_STALE'
        if h and h.get('state')=='ACTIVE' and not transport_ok:fault='WS_HEARTBEAT_STALE'
        if fault:
            if self.last_fault==0 or fault!=self.reason or self.was_ready:self.gap_epoch+=1
            self.last_fault=at;self.reason=fault;self.was_ready=False;self.ready_since=0
        state='STARTING';reason='WAITING_NATIVE_SOURCE'
        if self.hard:state='QUARANTINED';reason=self.hard
        elif d.get('engine_status') in ('DONE','INCOMPLETE'):state='STOPPED';reason='NATIVE_CAPTURE_ENDED'
        elif native_ok and transport_ok:
            if self.last_fault and at-self.last_fault<WARMUP_MS:state='RECOVERING';reason='ACTOR_WARMUP_AFTER_EVENT_GAP'
            else:
                state='ACTIVE';reason='OK'
                if not self.was_ready:
                    if self.last_fault:self.recoveries+=1
                    self.ready_since=at;self.was_ready=True
        elif fault:state='DEGRADED';reason=fault
        return dict(state=state,reason=reason,generation=generation,actor_epoch=self.gap_epoch,actor_ready=state=='ACTIVE',
                    price_ready=native_ok and transport_ok and not self.hard,ready_since_ms=self.ready_since,
                    last_fault_ms=self.last_fault,warmup_remaining_ms=max(0,WARMUP_MS-(at-self.last_fault)) if self.last_fault else 0,
                    recoveries=self.recoveries,integrity=self.hard is None,continuous_lifetime_integrity=self.last_fault==0)

def run(session,symbol,binary):
    session=Path(session);root=session/'sources'/symbol;root.mkdir(parents=True,exist_ok=True);status=root/'status.json';stop=Stop().install()
    cfg=read_json(session/'spot_prep'/symbol/'MANIFEST.json');conn=read_json(session/'spot_prep/CONNECTION.json');relay=read_json(session/'rpc_relay/ENDPOINT.json');ws_relay=read_json(session/'ws_relay/ENDPOINT.json')
    if not cfg or len(cfg.get('pools',[]))!=1 or cfg['pools'][0]['symbol']!=symbol or not relay or not ws_relay:raise RuntimeError('SINGLE_SOURCE_MANIFEST_OR_RELAY_MISSING')
    evidence=root/('m2-'+symbol);evidence.mkdir();health=root/'ws_health.json'
    env=clean_env();env.update(BELIAL_A1_SENSOR='1',BELIAL_A1_HEALTH_FILE=str(health),TOKIO_WORKER_THREADS='1',BELIAL_T3_POOL_MANIFEST=str(session/'spot_prep'/symbol/'MANIFEST.json'))
    argv=[str(binary),'--minutes',str(MINUTES),'--rpc',relay['url'],'--ws',ws_relay['url'],'--state',str(evidence/'dashboard_state.json'),'--evidence',str(evidence)]
    atomic(root/'COMMAND.json',{'argv':argv,'orders':False,'source':symbol,'cohort':cohort_of(symbol)})
    p,log=spawn(argv,root/'native.log',env=env);mon=Monitor();last=None;events=(root/'health_events.jsonl').open('a');blocked=None
    try:
        while not stop.value and not session_expired(session):
            d=read_json(evidence/'dashboard_state.json',{}) or {};h=read_json(health,{}) or {};now=now_ms();row=mon.update(d,h,now)
            if p.poll() is not None:blocked='NATIVE_EXIT:'+str(p.returncode);row.update(state='QUARANTINED',reason=blocked,integrity=False,actor_ready=False)
            if row['state']=='QUARANTINED':blocked=row['reason']
            signature=(row['state'],row['reason'],row['generation'],row['actor_epoch'])
            if signature!=last:events.write(canonical(dict(row,at_ms=now,symbol=symbol))+'\n');events.flush();last=signature
            state=row.pop('state')
            heartbeat(status,state,**row,swaps=int(d.get('swaps_total',0)),wallets=int(d.get('unique_wallets',0)),rpc_errors=int(d.get('rpc_errors',0)),
                      dropped_events=int(d.get('dropped_events',0)),pending_actor=int(d.get('actor_queue_depth',0)),native_pid=p.pid,native_evidence=str(evidence),network_at_ms=h.get('at_ms'),cohort=cohort_of(symbol))
            if blocked:break
            time.sleep(.25)
    finally:
        (evidence/'REQUEST_STOP').touch()
        try:p.wait(timeout=32)
        except subprocess.TimeoutExpired:terminate(p,5)
        log.close();events.close()
        heartbeat(status,'QUARANTINED' if blocked else 'STOPPED',reason=blocked or 'SESSION_ENDED',actor_ready=False,generation=mon.previous[1] if mon.previous else 0,integrity=not blocked,recoveries=mon.recoveries)
        atomic(root/'COMPLETE.json',{'ok':blocked is None,'reason':blocked,'native_returncode':p.returncode,'orders':False,'recoveries':mon.recoveries,'gap_epochs':mon.gap_epoch})
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);ap.add_argument('--symbol',choices=SYMBOLS,required=True);ap.add_argument('--binary',type=Path,required=True);a=ap.parse_args()
    try:raise SystemExit(run(a.session,a.symbol,a.binary))
    except Exception as e:heartbeat(a.session/'sources'/a.symbol/'status.json','QUARANTINED',reason=str(e),generation=0,integrity=False,actor_ready=False);raise
