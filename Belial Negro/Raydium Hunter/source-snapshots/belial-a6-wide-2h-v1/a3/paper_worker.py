"""Independent PAPER accounting and per-venue ingest. Dashboard is another process."""
from __future__ import annotations
import argparse,collections,hashlib,queue,threading,time,subprocess
from a3.common import *
from a3.journal import Journal
from a3.paper_controller import Controller
from a3.venue_worker import public_json,Denied
add_legacy('perp')
import markets

def run(session,binary):
    session=Path(session);out=session/'paper';out.mkdir(exist_ok=True);stop=Stop().install()
    cfg=read_json(LEGACY/'perp/CONFIG_PAPER.json');cfg['external_costs']=read_json(session/'COSTS_FROZEN.json') or read_json(ROOT/'CONFIG_CUSTOS_EXTERNOS.json');atomic(out/'CONFIG_EFFECTIVE.json',cfg,durable=True)
    outputs=Journal(out/'outputs.jsonl');inputs=Journal(out/'inputs.jsonl')
    ctl=Controller(cfg,lambda k,r:outputs.append({'kind':k,'body':r}));seq=0;last_at=0
    streams={v:queue.Queue(maxsize=4096) for v in ('hyperliquid','lighter')};proc={};logs={};native_errors={};threads=[]
    def read_stream(v,p):
        try:
            for line in iter(p.stdout.readline,b''):
                if len(line)>(32<<20):raise RuntimeError('ENVELOPE_TOO_LARGE')
                row=strict_loads(line)
                try:streams[v].put(row,timeout=2)
                except queue.Full:raise RuntimeError('INGEST_BACKPRESSURE_NO_SILENT_DROP')
                if stop.value:return
        except Exception as e:native_errors[v]=str(e)
        finally:native_errors.setdefault(v,'VENUE_PROCESS_ENDED')
    for v in streams:
        logs[v]=(out/(v+'.log')).open('w')
        p=subprocess.Popen(command('venue_worker','--venue',v,'--session',session,'--binary',binary),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                           stderr=logs[v],env=clean_env(),cwd=ROOT,start_new_session=True)
        proc[v]=p;t=threading.Thread(target=read_stream,args=(v,p),daemon=True);t.start();threads.append(t)
    def reconnect(v):
        p=proc.get(v)
        if p and p.poll() is None:
            try:p.stdin.write((canonical({'reconnect':v})+'\n').encode());p.stdin.flush()
            except OSError:pass
    ctl.books.reconnect=reconnect
    def process(kind,body,source='control',epoch=0,number=None):
        nonlocal seq,last_at
        seq+=1;at=max(now_ms(),last_at);last_at=at
        event={'kind':kind,'body':body,'source':source,'epoch':epoch,'sequence':number if number is not None else seq,'at_ms':at}
        try:inputs.append(event)
        except Exception as e:ctl.audit_ok=False;ctl.error='INPUT_AUDIT_FAILED:'+str(e)
        ctl.apply(event)
    source_tails={g:JsonTail(session/('aggregate/'+folder+'/events.jsonl')) for g,folder in [('CORE12','shadow_compare'),('EXPANSION12','shadow_expansion')]};last_fast=last_slow=last_status=last_tick=0
    frozen=False;seen_status={};latency=collections.deque(maxlen=2000);window=collections.deque();messages=0;failure=None
    funding_q=queue.Queue(maxsize=128);fund_stop=threading.Event();fund_started=False;consumed_errors=set()
    def funding_loop():
        hl=[m for m in ctl.markets.get('hyperliquid',[])]
        while not fund_stop.is_set():
            for m in hl:
                at=now_ms()
                try:
                    values=public_json(markets.HL_INFO,{'type':'fundingHistory','coin':m['market'],'startTime':(at//3600000-1)*3600000})
                    if not isinstance(values,list):raise ValueError('FUNDING_RESPONSE_SCHEMA')
                    for r in values:
                        if r.get('coin')==m['market'] and int(r['time'])<=now_ms():
                            funding_q.put({'key':['hyperliquid',m['symbol']],'at':int(r['time']),'rate':float(r['fundingRate']),
                                          'known_ms':now_ms(),'origin':'HYPERLIQUID_FUNDING_HISTORY'},timeout=2)
                except Denied:
                    heartbeat(out/'funding_status.json','ACCESS_DENIED');return
                except Exception as e:heartbeat(out/'funding_status.json','DEGRADED',error=str(e))
                if fund_stop.wait(.2):return
            if fund_stop.wait(30):return
    try:
        while not stop.value:
            state=session_state(session)
            if state.get('phase')=='CAPTURING' and not frozen:
                process('cohort',{'cohort':read_json(session/'COHORT.json'), 'started_ms':state['started_ms'],'duration_ms':DURATION_MS});frozen=True
                threading.Thread(target=funding_loop,daemon=True).start();fund_started=True
            for v,q in streams.items():
                for _ in range(128):
                    try:event=q.get(timeout=.002 if _==0 else 0)
                    except queue.Empty:break
                    if event['kind']=='markets' and frozen:
                        # Late metadata is archived, but not silently added to the frozen experiment.
                        atomic(out/(v+'_LATE_METADATA.json'),event['body']);continue
                    process(event['kind'],event['body'],event['source'],event['epoch'],event['sequence'])
                    if event['kind']=='book':
                        messages+=1;t=now_ms();window.append(t);latency.append(max(0,(time.time_ns()-int(event['body']['received_ns']))/1e6))
            for v,error in list(native_errors.items()):
                if v not in consumed_errors:
                    consumed_errors.add(v)
                    threading.Thread(target=terminate,args=(proc[v],3),daemon=True).start()
                    process('book',{'venue':v,'received_ns':time.time_ns(),'generation':9_000_000_000,'transport':'DISCONNECTED'},'terminal_'+v)
            t=now_ms()
            if t-last_status>=100:
                for s in SYMBOLS:
                    status=read_json(session/'sources'/s/'status.json',{}) or {}
                    analysis_bad=read_json(session/'ANALYSIS_BLOCKED.json',{}).get(s)
                    analysis_health=read_json(session/'aggregate/status.json',{}) or {}
                    if analysis_bad or (frozen and (analysis_health.get('state')!='ACTIVE' or t-analysis_health.get('at_ms',0)>5000)):
                        status=dict(status,state='QUARANTINED',reason=analysis_bad or 'ANALYSIS_NOT_ACTIVE',integrity=False)
                    if status!=seen_status.get(s):process('source_status',{'symbol':s,'status':status});seen_status[s]=status
                platform=read_json(session/'INTEGRITY.json',{'ok':True})
                if platform.get('ok') is not True and ctl.audit_ok:process('integrity',platform)
                last_status=t
            for group,tail in source_tails.items():
                for offset,row in tail.read(256):
                    if row.get('kind')=='EPISODE_OPEN':
                        if cohort_of(row['symbol'])!=group:raise ValueError('SIGNAL_COHORT_MISMATCH')
                        process('signal',row,'shadow_signals_'+group,0,offset+1)
            for _ in range(32):
                try:row=funding_q.get_nowait()
                except queue.Empty:break
                process('funding',row)
            if frozen and t-last_tick>=20:process('tick',{});last_tick=t
            while window and window[0]<t-1000:window.popleft()
            if t-last_fast>=40:
                sorted_lat=sorted(latency)
                books={'at_ms':t,'books':[b.view(t) for b in ctl.books.books.values()],'transport':ctl.books.transport,
                       'ingest_messages':messages,'messages_per_second':len(window),'ingest_queue':sum(q.qsize() for q in streams.values()),
                       'rust_to_paper_ms_p50':sorted_lat[len(sorted_lat)//2] if sorted_lat else None,
                       'rust_to_paper_ms_p95':sorted_lat[int((len(sorted_lat)-1)*.95)] if sorted_lat else None,
                       'book_errors':ctl.books.errors[-8:]}
                atomic(out/'books.json',books);last_fast=t
            if t-last_slow>=250:
                snapshot=ctl.snapshot(t)
                coverage={s:{v:any(m['symbol']==s for m in ctl.markets.get(v,[])) for v in streams} for s in SYMBOLS}
                snapshot.update(config=cfg,session=session.name,market_coverage=coverage,
                                main=read_json(session/'aggregate/m2_tail/state.json',{}),
                                shadow=read_json(session/'aggregate/shadow_compare/state.json',{}),
                                n2=read_json(session/'n2/status.json',{}),shadow_expansion=read_json(session/'aggregate/shadow_expansion/state.json',{}),heads=read_json(ROOT/'n2_a2/CONFIG_N2.json')['heads'])
                from a3.terminal_projection import compact_state
                atomic(out/'state.json',compact_state(snapshot));heartbeat(out/'status.json','ACTIVE' if ctl.audit_ok else 'INTEGRITY_BLOCKED',
                        messages=messages,venues=list(ctl.markets),error=ctl.error,metadata_finished=all(v in ctl.markets or (proc[v].poll() is not None and streams[v].empty()) for v in streams))
                last_slow=t
            if session_expired(session):break
        process('finalize',{})
        # Funding-only finalization window. Never accepts post-deadline books,
        # signals or fills, never extends the 90-minute experiment.
        grace=time.monotonic()+60
        while not stop.value and time.monotonic()<grace and any(p.funding_total(p.closed_ms or ctl.deadline)[1] for engine in ctl.engines.values() for p in engine.positions.values()):
            try:row=funding_q.get(timeout=.5);process('funding',row)
            except queue.Empty:pass
            settling=read_json(out/'state.json',{}) or {};settling.update(ctl.snapshot(min(now_ms(),ctl.deadline)),complete=True,settlement_only=True)
            atomic(out/'state.json',settling)
            heartbeat(out/'status.json','SETTLEMENT_ONLY_NO_NEW_FILLS',seconds_remaining=max(0,round(grace-time.monotonic())))

    except BaseException as e:
        failure=str(e);ctl.audit_ok=False;ctl.error=failure
        heartbeat(out/'status.json','FAILED_NO_AUTOMATIC_POSITION_RESET',error=failure)
    finally:
        stop.value=True;fund_stop.set()
        for tail in source_tails.values():tail.close()
        for v,p in proc.items():
            try:p.stdin.write(b'{"stop":true}\n');p.stdin.flush()
            except Exception:pass
            terminate(p,8);logs[v].close()
        for thread in threads:thread.join(timeout=2)
        for j in (inputs,outputs):
            try:j.close()
            except Exception as e:failure=str(e);ctl.audit_ok=False
        t=now_ms();final=ctl.snapshot(t)
        existing=read_json(out/'state.json',{}) or {};existing.update(final);existing['complete']=True
        atomic(out/'state.json',existing,durable=True)
        atomic(out/'COMPLETE.json',{'ok':failure is None and ctl.audit_ok,'error':failure,'integrity':ctl.audit_ok,
                                  'input_sha256':digest(out/'inputs.jsonl'),'output_sha256':digest(out/'outputs.jsonl'),
                                  'positions_preserved':True,'orders':False},durable=True)
    return 0 if failure is None else 2

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);ap.add_argument('--binary',type=Path,required=True);a=ap.parse_args()
    return run(a.session,a.binary)
if __name__=='__main__':raise SystemExit(main())
