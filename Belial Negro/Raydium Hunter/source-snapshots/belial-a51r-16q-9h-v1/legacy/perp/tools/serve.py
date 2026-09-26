#!/usr/bin/env python3
"""Isolated T3 PAPER service: Rust WebSockets -> validated books -> paper ledger -> SSE.
Only loopback GET endpoints, no trading controls. All disk writes stay in T3 evidence.
"""
from __future__ import annotations
import argparse,collections,gzip,json,os,queue,signal,subprocess,sys,threading,time,hashlib,io
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from books import Books
from paper import Engine
import markets
STOP=threading.Event()

def now():return time.time_ns()//1000000

def atomic(path,data):
 path=Path(path);tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n');os.replace(tmp,path)

def read_json(path,default=None):
 try:return json.loads(Path(path).read_text())
 except (OSError,ValueError):return default

class Journal:
 def __init__(self,root):
  self.root=root;self.queue=queue.Queue(maxsize=8192);self.error=None;self.max_queue=0;self.t=threading.Thread(target=self.work,name='t3-audit',daemon=True);self.t.start()
 def write(self,kind,row):
  if self.error:raise RuntimeError('AUDIT_WRITER_FAILED:'+self.error)
  self.max_queue=max(self.max_queue,self.queue.qsize())
  try:self.queue.put((kind,row),timeout=1)
  except queue.Full:raise RuntimeError('AUDIT_BACKPRESSURE_NO_SILENT_DROP')
 def work(self):
  files={}
  try:
   while True:
    item=self.queue.get()
    if item is None:break
    kind,row=item
    if kind not in files:
     files[kind]=gzip.open(self.root/(kind+'.jsonl.gz'),'at',encoding='utf-8',compresslevel=1) if kind=='raw_ws' else (self.root/(kind+'.jsonl')).open('a',encoding='utf-8')
    files[kind].write(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    if kind in ('orders','trades','signals','funding'):files[kind].flush()
  except Exception as e:self.error=str(e)
  finally:
   for f in files.values():
    try:f.close()
    except Exception as e:self.error=str(e)
 def close(self):
  if self.t.is_alive():self.queue.put(None,timeout=2);self.t.join(timeout=15)
  if self.t.is_alive() or self.error:raise RuntimeError('AUDIT_FLUSH_FAILED:'+str(self.error))

class Bus:
 def __init__(self):self.cv=threading.Condition();self.seq=0;self.payloads={};self.clients=0
 def publish(self,kind,data):
  text=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False)
  with self.cv:self.seq+=1;self.payloads[kind]=(self.seq,text);self.cv.notify_all()

def make_server(bus,html):
 class Handler(BaseHTTPRequestHandler):
  protocol_version='HTTP/1.1'
  def log_message(self,*args):pass
  def do_GET(self):
   if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):self.send_error(403);return
   if self.path=='/':
    payload=html.read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
   if self.path=='/health':
    data=b'{"ok":true,"paper_only":true,"orders":false}';self.send_response(200);self.send_header('Content-Length',str(len(data)));self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data);return
   if self.path!='/events':self.send_error(404);return
   with bus.cv:
    if bus.clients>=4:self.send_error(429);return
    bus.clients+=1
   try:
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.send_header('Connection','keep-alive');self.end_headers();last={};heartbeat=0.
    while not STOP.is_set():
     with bus.cv:
      bus.cv.wait(timeout=.1);updates=[(kind,seq,text) for kind,(seq,text) in bus.payloads.items() if seq!=last.get(kind)]
     for kind,seq,text in sorted(updates,key=lambda x:x[1]):
      self.wfile.write(f'event: {kind}\nid: {seq}\ndata: {text}\n\n'.encode());last[kind]=seq
     if time.monotonic()-heartbeat>=5:self.wfile.write(b': keepalive\n\n');heartbeat=time.monotonic()
     self.wfile.flush()
   except (OSError,ConnectionError):pass
   finally:
    with bus.cv:bus.clients-=1
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
 threading.Thread(target=server.serve_forever,name='t3-http',daemon=True).start();return server

class Tail:
 def __init__(self,path):self.path=Path(path);self.file=None;self.ident=None
 def read(self,limit=2048):
  if self.file is None:
   if not self.path.exists():return []
   self.file=self.path.open('rb');st=self.path.stat();self.ident=st.st_dev,st.st_ino
  st=self.path.stat()
  if (st.st_dev,st.st_ino)!=self.ident or st.st_size<self.file.tell():raise RuntimeError('EVIDENCE_ROTATED_OR_TRUNCATED')
  out=[]
  while len(out)<limit:
   at=self.file.tell();line=self.file.readline(1048577)
   if not line:break
   if len(line)>1048576:raise RuntimeError('EVENT_TOO_LARGE')
   if not line.endswith(b'\n'):self.file.seek(at);break
   row=json.loads(line)
   if not isinstance(row,dict):raise RuntimeError('EVENT_NOT_OBJECT')
   out.append(row)
  return out
 def close(self):
  if self.file:self.file.close()


def funding_worker(manifest,events):
 """Historical public funding rates are used only after publication, never prediction."""
 hl=[m for m in manifest['markets'] if m['venue']=='hyperliquid']
 while not STOP.is_set():
  for m in hl:
   if STOP.is_set():return
   at=now()
   try:
    values=markets.http(markets.HL_INFO,{'type':'fundingHistory','coin':m['market'],'startTime':(at//3600000-1)*3600000})
    if not isinstance(values,list):raise ValueError('FUNDING_HISTORY_SCHEMA')
    for r in values:
     if r.get('coin')==m['market'] and int(r['time'])<=now():events.put(('funding',{'key':('hyperliquid',m['symbol']),'at':int(r['time']),'rate':float(r['fundingRate']),'known_ms':now()}),timeout=2)
   except Exception as e:
    try:events.put(('funding_error',{'venue':'hyperliquid','symbol':m['symbol'],'error':str(e),'at_ms':now()}),timeout=2)
    except queue.Full:return
   if STOP.wait(.15):return
  if STOP.wait(30):return

def run(a):
 duration_ms=int(getattr(a,'duration_ms',max(1,a.deadline_ms-now())))
 if duration_ms<=0:raise RuntimeError('INVALID_CAPTURE_DURATION')
 root=Path(__file__).resolve().parents[1];cfg=json.loads((root/'CONFIG_PAPER.json').read_text());manifest=json.loads(a.markets.read_text());out=a.evidence/'perp_t3';out.mkdir(parents=True,exist_ok=False)
 atomic(out/'CONFIG_EFFECTIVE.json',cfg);atomic(out/'MARKETS.json',manifest)
 pipe_log=(out/'rust_transport.log').open('w');proc=subprocess.Popen([str(a.binary),str(a.markets)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=pipe_log,bufsize=0)
 events=queue.Queue(maxsize=8192);journal=Journal(out);bus=Bus();server=make_server(bus,root/'web/dashboard.html');url=f'http://127.0.0.1:{server.server_port}/';(out/'dashboard_url.txt').write_text(url+'\n');print('DASHBOARD_URL='+url,flush=True)
 def reconnect(venue):
  if proc.poll() is None:
   try:proc.stdin.write((json.dumps({'reconnect':venue})+'\n').encode());proc.stdin.flush()
   except BrokenPipeError:pass
 books=Books(manifest,reconnect);engine=Engine(books,cfg,journal.write);source=Tail(a.evidence/'shadow_compare/events.jsonl')
 def reader():
  try:
   with io.BufferedReader(proc.stdout,buffer_size=1024*1024) as stream:
    for line in stream:
     if len(line)>32*1024*1024:raise RuntimeError('WS_FRAME_TOO_LARGE')
     try:events.put(('ws',json.loads(line)),timeout=2)
     except queue.Full:raise RuntimeError('INGEST_BACKPRESSURE_NO_SILENT_DROP')
     if STOP.is_set():return
   events.put(('fatal',{'error':'RUST_TRANSPORT_ENDED'}),timeout=1)
  except Exception as e:
   try:events.put(('fatal',{'error':str(e)}),timeout=1)
   except queue.Full:STOP.set()
 reader_thread=threading.Thread(target=reader,name='t3-feed',daemon=True);reader_thread.start();threading.Thread(target=funding_worker,args=(manifest,events),name='t3-funding',daemon=True).start()
 seq=0;last_fast=last_slow=last_signal=0;started=now();metrics=collections.deque(maxlen=2000);window=collections.deque();max_queue=0;failure=None;latest={};latest_books={};state_seq=0
 try:
  while not STOP.is_set():
   t=now();max_queue=max(max_queue,events.qsize())
   for i in range(512):
    try:kind,row=events.get(timeout=.004 if i==0 else 0)
    except queue.Empty:break
    t=now()
    if kind=='fatal':raise RuntimeError(row['error'])
    if kind=='ws':
     seq+=1;window.append(t);received=int(row['received_ns'])//1000000;metrics.append(max(0.,(time.time_ns()-int(row['received_ns']))/1e6))
     if cfg['record_raw_ws']:journal.write('raw_ws',row)
     keys=books.ingest(row)
     # Context messages and book changes are separate; only books can execute fills.
     for key,b in books.books.items():engine.record_context(key,b,t)
     for key in keys:engine.on_book(key,t)
    elif kind=='funding':engine.funding_rate(tuple(row['key']),row['at'],row['rate'],row['known_ms'],'HYPERLIQUID_FUNDING_HISTORY')
    elif kind=='funding_error':journal.write('funding_errors',row)
   t=now();engine.tick(t)
   if journal.error:raise RuntimeError('AUDIT_FAILED:'+journal.error)
   if t-last_signal>=10:
    for signal_row in source.read():engine.signal(signal_row,t)
    last_signal=t
   while window and window[0]<t-1000:window.popleft()
   if t-last_fast>=1000/int(cfg['dashboard_push_hz']):
    latest_books={'at_ms':t,'books':[b.view(t) for b in books.books.values()],'transport':books.transport,'ingest_messages':seq,'messages_per_second':len(window),'ingest_queue':events.qsize(),'max_ingest_queue':max_queue,'audit_queue':journal.queue.qsize(),'rust_to_paper_ms_p50':sorted(metrics)[len(metrics)//2] if metrics else None,'rust_to_paper_ms_p95':sorted(metrics)[int((len(metrics)-1)*.95)] if metrics else None,'book_errors':books.errors[-5:]};bus.publish('books',latest_books);last_fast=t
   if t-last_slow>=500:
    state_seq+=1;latest=engine.snapshot(t);latest.update({'state_sequence':state_seq,'session':a.evidence.name,'deadline_ms':a.deadline_ms,'duration_ms':duration_ms,'started_ms':a.deadline_ms-duration_ms,'service_started_ms':started,'config':cfg,'market_coverage':manifest['coverage'],'metadata_failures':manifest.get('metadata_failures',{}),'main':read_json(a.evidence/'m2_tail/state.json',{}) or {},'shadow':read_json(a.evidence/'shadow_compare/state.json',{}) or {},'n2':read_json(a.n2_runtime/'status.json',{}) or {},'heads':json.loads(a.n2_config.read_text())['heads']})
    bus.publish('state',latest);atomic(out/'state.json',latest);last_slow=t
   if (a.evidence/'SESSION_COMPLETE').exists() or t>=a.deadline_ms:
    engine.finalize(t);latest=engine.snapshot(t)|{k:latest.get(k) for k in ('session','deadline_ms','duration_ms','started_ms','service_started_ms','config','market_coverage','metadata_failures','main','shadow','n2','heads')};latest['complete']=True;bus.publish('state',latest);atomic(out/'state.json',latest);break
   if t-started>30000 and not any(x.updates for x in books.books.values()):raise RuntimeError('NO_VALID_PUBLIC_BOOK_WITHIN_30_SECONDS')
   if proc.poll() is not None:raise RuntimeError('RUST_PERP_COLLECTOR_EXITED')
  if not engine.finalized:engine.finalize(now())
 except BaseException as e:failure=str(e);atomic(out/'ERROR.json',{'error':failure,'at_ms':now(),'orders':False});raise
 finally:
  STOP.set();source.close()
  try:
   proc.stdin.write(b'{"stop":true}\n');proc.stdin.flush();proc.stdin.close();proc.wait(timeout=8)
  except Exception:
   proc.terminate()
   try:proc.wait(timeout=3)
   except Exception:proc.kill();proc.wait()
  reader_thread.join(timeout=3);pipe_log.close();journal.close()
  status={'ok':failure is None,'error':failure,'frames_received':seq,'book_updates':{v+':'+s:b.updates for (v,s),b in books.books.items()},'nonce_gaps':sum(b.gaps for b in books.books.values()),'max_ingest_queue':max_queue,'max_audit_queue':journal.max_queue,'paper_only':True,'orders':False,'network_latency_is_assumption':True,'live_rate_measured_not_promised':True,'funding_not_account_settlement':True,'stopped_ms':now()};atomic(out/'SERVICE_COMPLETE.json',status)
  atomic(out/'books_final.json',latest_books)
  html=(root/'web/dashboard.html').read_text();embedded='<script>window.BELIAL_FINAL_STATE='+json.dumps(latest,ensure_ascii=False).replace('<','\\u003c')+';window.BELIAL_FINAL_BOOKS='+json.dumps(latest_books,ensure_ascii=False).replace('<','\\u003c')+';</script>'
  (out/'DASHBOARD_FINAL.html').write_text(html.replace('<!-- FINAL_STATE -->',embedded))
  server.shutdown();server.server_close()
 return 0

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--markets',type=Path,required=True);ap.add_argument('--binary',type=Path,required=True);ap.add_argument('--deadline-ms',type=int,required=True);ap.add_argument('--duration-ms',type=int,required=True);ap.add_argument('--n2-runtime',type=Path,required=True);ap.add_argument('--n2-config',type=Path,required=True);a=ap.parse_args()
 for p in (a.evidence,a.markets,a.binary,a.n2_config):
  if p.is_symlink() or not p.exists():raise RuntimeError('MISSING_OR_SYMLINK:'+str(p))
 signal.signal(signal.SIGTERM,lambda *_:STOP.set());signal.signal(signal.SIGINT,lambda *_:STOP.set());return run(a)
if __name__=='__main__':raise SystemExit(main())
