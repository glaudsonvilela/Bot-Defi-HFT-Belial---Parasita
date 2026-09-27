"""Bounded native IPC consumer; decisions recorded with feature/book evidence."""
import argparse,collections,contextlib,json,queue,subprocess,threading,time
from pathlib import Path
from a51.common import ROOT,Stop,atomic,heartbeat,now_ms,read_json,session_expired,terminate,clean_env
from a51.journal import Journal
from a51.cache import JsonCache
from a6.tails import Tails

def run(session):
    stop=Stop().install();out=session/'wide';out.mkdir(exist_ok=True);selection=out/'selection.json';atomic(selection,{'symbols':[]})
    while not stop.value and not session_expired(session):
        build=read_json(session/'build/RESULTS.json',{}) or {};native=build.get('wide')
        if native is not None:break
        heartbeat(out/'status.json','WAITING_NATIVE_BUILD',orders=False);time.sleep(.5)
    else:return 0
    if not native.get('ok'):
        heartbeat(out/'status.json','FAILED',reason='WIDE_NATIVE_BUILD_FAILED',orders=False);return 2
    events=Journal(out/'events.jsonl');engine=Tails(lambda r:events.append(r));q=queue.Queue(maxsize=4096);dropped=[0];closed=threading.Event()
    log=(out/'native.log').open('ab');proc=subprocess.Popen([native['path'],str(selection)],stdout=subprocess.PIPE,stderr=log,env=clean_env(),start_new_session=True)
    def reader():
        try:
            for line in iter(proc.stdout.readline,b''):
                if len(line)>8<<20:dropped[0]+=1;continue
                try:row=json.loads(line)
                except ValueError:dropped[0]+=1;continue
                try:q.put_nowait(row)
                except queue.Full:dropped[0]+=1
        finally:closed.set()
    thread=threading.Thread(target=reader,daemon=True);thread.start();last_view=last_select=0;last_drop=0;chosen=[];error=None;integrity_lost=False;cache=JsonCache()
    try:
        while not stop.value:
            now=now_ms();state=cache.get(session/'SESSION.json',{}) or {}
            if state.get('phase') in ('STOPPING','FINISHED','FAILED') or (state.get('deadline_monotonic_ns') and time.monotonic_ns()>=state['deadline_monotonic_ns']):break
            engine.start=state.get('started_ms');engine.deadline=state.get('deadline_ms')
            integrity=cache.get(session/'INTEGRITY.json',{}) or {}
            if integrity.get('ok') is not True:
                engine.start=None
                if not integrity_lost:engine.gap('all',now,'SESSION_INTEGRITY_LOST');integrity_lost=True
            if dropped[0]!=last_drop:
                engine.gap('all',now,'PYTHON_IPC_OVERFLOW');last_drop=dropped[0]
                while not q.empty():
                    with contextlib.suppress(queue.Empty):q.get_nowait()
            for _ in range(1024):
                try:row=q.get_nowait()
                except queue.Empty:break
                try:engine.ingest(row,now_ms())
                except (ValueError,KeyError,TypeError,OverflowError) as e:
                    engine.counts['invalid_envelope']+=1;engine.gap('all',now_ms(),'INVALID_NATIVE_ENVELOPE')
            now=now_ms()
            if now-last_select>=10000:
                chosen=engine.selected(now);atomic(selection,{'symbols':chosen,'at_ms':now,'orders':False});last_select=now
            if now-last_view>=1000:
                engine.tick(now,chosen);view=engine.view(now,chosen);view.update(native_pid=proc.pid,ipc_queue=q.qsize(),ipc_drops=dropped[0],phase=state.get('phase'))
                atomic(out/'state.json',view);heartbeat(out/'status.json','ACTIVE' if view['wide_fresh'] else 'WAITING_PUBLIC_DATA',catalog=view['catalog'],wide_fresh=view['wide_fresh'],deep_requested=len(chosen),fresh_books=view['fresh_books'],counts=view['counts'],orders=False);last_view=now
            if closed.is_set() and q.empty():raise RuntimeError('NATIVE_FEED_ENDED:'+str(proc.poll()))
            time.sleep(.01)
    except Exception as e:error=str(e);heartbeat(out/'status.json','FAILED',reason=error,orders=False)
    finally:
        terminate(proc,5);thread.join(2);log.close();engine.finish(now_ms());events.close();atomic(out/'state.json',dict(engine.view(now_ms(),chosen),complete=True,error=error));atomic(out/'COMPLETE.json',dict(ok=error is None,error=error,orders=False))
    return 0 if error is None else 2
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);raise SystemExit(run(p.parse_args().session))
