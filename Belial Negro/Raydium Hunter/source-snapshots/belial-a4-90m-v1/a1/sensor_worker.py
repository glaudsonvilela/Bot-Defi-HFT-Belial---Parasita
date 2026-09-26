"""Lifecycle of a single qualified Rust sensor. Faults never kill unrelated processes."""
from __future__ import annotations
import argparse
from a1.common import *
def classify(dashboard,health,at):
    if not dashboard:return 'STARTING','WAITING_NATIVE_DASHBOARD'
    if dashboard.get('engine_status') in ('DONE','INCOMPLETE'):return 'STOPPED','NATIVE_CAPTURE_ENDED'
    if any(int(dashboard.get(k,0)) for k in ('rpc_errors','dropped_events')):return 'QUARANTINED','RPC_ERROR_OR_DROPPED_EVENT'
    if not health:return 'STARTING','WAITING_WS_ACK'
    if int(health.get('generation',0))>0:return 'QUARANTINED','SOURCE_RECONNECTED_OBSERVATION_ONLY'
    if health.get('state')=='DISCONNECTED':return 'QUARANTINED','WS_DISCONNECTED'
    if health.get('state')!='ACTIVE':return 'STARTING','WAITING_WS_ACK'
    if not 0<=at-int(health.get('at_ms',0))<=45_000:return 'STALE','NO_RECENT_WS_FRAME_OR_PONG'
    if not 0<=at-int(dashboard.get('updated_unix_ms',0))<=5_000:return 'STALE','NATIVE_DASHBOARD_STALE'
    return 'ACTIVE','OK'
def run(session,symbol,binary):
    session=Path(session);root=session/'sources'/symbol;root.mkdir(parents=True,exist_ok=True);status=root/'status.json';stop=Stop().install()
    cfg=read_json(session/'spot_prep'/symbol/'MANIFEST.json');connection=read_json(session/'spot_prep/CONNECTION.json');relay=read_json(session/'rpc_relay/ENDPOINT.json')
    if not cfg or len(cfg.get('pools',[]))!=1 or cfg['pools'][0]['symbol']!=symbol or not relay:raise RuntimeError('SINGLE_SOURCE_MANIFEST_OR_RELAY_MISSING')
    evidence=root/('m2-'+symbol);evidence.mkdir();health=root/'ws_health.json'
    env=clean_env();env.update(BELIAL_A1_SENSOR='1',BELIAL_A1_HEALTH_FILE=str(health),TOKIO_WORKER_THREADS='1',BELIAL_T3_POOL_MANIFEST=str(session/'spot_prep'/symbol/'MANIFEST.json'))
    argv=[str(binary),'--minutes',str(MINUTES),'--rpc',relay['url'],'--ws',connection['ws'],'--state',str(evidence/'dashboard_state.json'),'--evidence',str(evidence)]
    atomic(root/'COMMAND.json',{'argv':argv,'orders':False,'source':symbol})
    p,log=spawn(argv,root/'native.log',env=env);blocked=None
    try:
        while not stop.value and not session_expired(session):
            d=read_json(evidence/'dashboard_state.json',{});h=read_json(health,{})
            state,reason=classify(d,h,now_ms())
            if p.poll() is not None:state,reason='QUARANTINED','NATIVE_EXIT:'+str(p.returncode)
            if state in ('QUARANTINED','STALE','STOPPED'):blocked=reason
            if blocked:state,reason='QUARANTINED',blocked
            heartbeat(status,state,reason=reason,generation=int(h.get('generation',0)),integrity=not blocked,
                      swaps=int(d.get('swaps_total',0)),wallets=int(d.get('unique_wallets',0)),rpc_errors=int(d.get('rpc_errors',0)),
                      pending_actor=int(d.get('actor_queue_depth',0)),native_pid=p.pid,native_evidence=str(evidence),
                      network_at_ms=h.get('at_ms'),qualification='CONTRACT_AND_ADAPTER_NOT_TOKEN_RISK_CERTIFICATE')
            if blocked:break
            time.sleep(.25)
    finally:
        (evidence/'REQUEST_STOP').touch()
        try:p.wait(timeout=32)
        except subprocess.TimeoutExpired:terminate(p,5)
        log.close();heartbeat(status,'QUARANTINED' if blocked else 'STOPPED',reason=blocked or 'SESSION_ENDED',generation=0,integrity=not blocked,native_returncode=p.returncode)
        atomic(root/'COMPLETE.json',{'ok':blocked is None,'reason':blocked,'native_returncode':p.returncode,'orders':False})
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);ap.add_argument('--symbol',choices=SYMBOLS,required=True);ap.add_argument('--binary',type=Path,required=True);a=ap.parse_args()
    try:raise SystemExit(run(a.session,a.symbol,a.binary))
    except Exception as e:heartbeat(a.session/'sources'/a.symbol/'status.json','QUARANTINED',reason=str(e),generation=0,integrity=False);raise
