#!/usr/bin/env python3
from __future__ import annotations
import argparse,collections,fcntl,hashlib,json,math,os,select,shutil,signal,subprocess,sys,time,zipfile
from pathlib import Path
from rpc_selection import connection_for_session
from token_capabilities import verify_manifest
from rpc_transport import archive_file, public_endpoint
from session_config import CAPTURE_MINUTES, CAPTURE_SECONDS, CAPTURE_MS, deadline_ns as capture_deadline_ns, watchdog_expired, format_countdown

BASE=Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter')
MP12=BASE/'releases/r5d-mp12-live-v1-fix4-90m'
N2=BASE/'releases/r5d-n2-gpu-mp12-v1'
D2=BASE/'releases/r5d-d2-ultra-v1'
PERP=BASE/'releases/r5d-t3-perp-90m-fix4-v1'
RUNREL=BASE/'releases/r5d-t3-90m-mp12-ultra-v1-fix4'
RT=BASE/'runtime/r5d-t3-90m-mp12-ultra'
OLD_N2=BASE/'releases/r5d-n2-gpu-wide-v1'
OLD_D1=BASE/'releases/r5d-d1-dashboard-v1'
DESKTOP=Path.home()/'Área de trabalho/Altside'
POOLS=('FARTCOIN', 'BOME', 'WIF', 'POPCAT', 'RAY', 'JUP', 'BONK', 'PENGU', 'PUMP', 'USELESS', 'GRASS', 'PYTH');OLD_POOLS={'FARTCOIN','BOME','WIF'}
STOP=False

def stop(*_):
 global STOP;STOP=True

def stamp():return time.strftime('%Y%m%dT%H%M%S',time.gmtime())+f'{time.time_ns()%1_000_000_000:09d}Z'
def now_ms():return time.time_ns()//1_000_000
def read_json(p,default=None):
 try:return json.loads(Path(p).read_text())
 except Exception:return default

def atomic(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.tmp-'+str(os.getpid()));t.write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n');os.replace(t,p)
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def alive(pid):
 try:os.kill(pid,0);return True
 except Exception:return False

def pid_from(p):
 try:return int(Path(p).read_text().strip())
 except Exception:return None

def old_states():
 n=pid_from(BASE/'runtime/r5d-n2-gpu/watch.pid');d=pid_from(BASE/'runtime/r5d-d1/dashboard.pid')
 return {'n2':bool(n and alive(n)),'d1':bool(d and alive(d)),'n2_pid':n,'d1_pid':d}

def control(rel,args,timeout=30):
 if not rel.is_dir():return None
 p=subprocess.run([sys.executable,'-B',str(rel/'tools/control.py'),*args],capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('CONTROL_FAILED:'+str(rel)+':'+p.stdout+p.stderr)
 return p.stdout.strip()

def active_collectors():
 out=[]
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   exe=(p/'exe').resolve(strict=True)
   if exe.is_relative_to(BASE) and ('live-profiler' in exe.name or 'mp12' in exe.name):out.append((int(p.name),str(exe)))
  except Exception:pass
 return out

def cpu_sample(prev):
 vals=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]));tot=sum(vals);idle=vals[3]+(vals[4] if len(vals)>4 else 0);pct=None
 if prev:
  dt=tot-prev[0];di=idle-prev[1]
  if dt>0:pct=max(0.,min(100.,100.*(dt-di)/dt))
 return (tot,idle),pct

def mem_gib():
 for l in Path('/proc/meminfo').read_text().splitlines():
  if l.startswith('MemAvailable:'):return int(l.split()[1])/1024/1024
 return None

def gpu():
 try:
  q='utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw'
  p=subprocess.run(['nvidia-smi','--query-gpu='+q,'--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=1.5,check=True);v=[x.strip() for x in p.stdout.splitlines()[0].split(',')]
  return {'util_pct':float(v[0]),'vram_used_mib':float(v[1]),'vram_total_mib':float(v[2]),'temperature_c':float(v[3]),'power_w':float(v[4])}
 except Exception as e:return {'error':str(e)}

def append(p,obj):
 with Path(p).open('a',encoding='utf-8') as f:f.write(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n');f.flush()

def start_n2(log,evidence):
 rt=BASE/'runtime/r5d-n2-gpu-mp12';rt.mkdir(parents=True,exist_ok=True);(rt/'STOP_N2').unlink(missing_ok=True)
 # A stale prior status must not qualify the new GPU process.
 watch_lock=(rt/'watch.lock').open('a+')
 try:fcntl.flock(watch_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise RuntimeError('T3_N2_ALREADY_RUNNING')
 (rt/'status.json').unlink(missing_ok=True);watch_lock.close()
 f=Path(log).open('ab',buffering=0);p=subprocess.Popen([sys.executable,'-B',str(N2/'tools/live.py'),'--base',str(BASE),'--runtime',str(rt),'--only-evidence',str(evidence)],stdout=f,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
 deadline=time.time()+35
 while time.time()<deadline:
  if p.poll() is not None:raise RuntimeError('N2_MP12_EXITED_EARLY:'+str(log))
  st=read_json(rt/'status.json',{}) or {}
  if st.get('state') not in (None,'GPU_INITIALIZING'):
   dim=int(st.get('dimension') or (st.get('gpu_benchmark') or {}).get('selected_dimension') or 0);heads=int(st.get('active_heads') or (st.get('gpu_benchmark') or {}).get('selected_heads') or 0)
   if dim!=8192 or heads!=32:raise RuntimeError('N2_MP12_STAGE_TOO_LOW:'+json.dumps(st))
   return p,st,f
  time.sleep(.1)
 raise RuntimeError('N2_MP12_INIT_TIMEOUT')

def stop_n2(p):
 rt=BASE/'runtime/r5d-n2-gpu-mp12';(rt/'STOP_N2').write_text('stop\n')
 if p and p.poll() is None:
  p.terminate()
  try:p.wait(timeout=8)
  except subprocess.TimeoutExpired:p.kill();p.wait()

def main_pnl_text(st):
 vals=[]
 for pid,k in [('TAIL_STOP50_TRAIL35','S50'),('TAIL_STOP75_TRAIL50','S75'),('TAIL_STOP100_TRAIL75','S100')]:
  p=(st.get('policies') or {}).get(pid) or {};sc=p.get('scenarios') or {};x=(sc.get('50') or {}).get('normalized_pnl_per_100_per_entry');vals.append(k+'='+('—' if x is None else f'{float(x):+.3f}'))
 return ' '.join(vals)

def reference(row):
 try:
  if row.get("protocol") is not None:
   from protocol_reference import audit_row
   return float(audit_row(row)["reference_post"])
  x=float(row['pool_meme_reserve']);y=float(row['pool_quote_reserve_sol']);q=float(row['meme_qty']);sol=float(row['quote_sol']);side=row['side']
  xp,yp=(x+q,y-sol) if side=='SELL' else (x-q,y+sol)
  if min(xp,yp)<=0:return None
  return yp/xp
 except Exception:return None

def validate(ev,engine_rc,n1_ok):
 count=0;by=collections.Counter();lat=[];refok=0;bad=[]
 with (ev/'swaps.jsonl').open() as f:
  for line in f:
   r=json.loads(line);count+=1;by[str(r.get('symbol'))]+=1
   if r.get('schema')!='belial_m2_swap_v1' or r.get('collector_variant')!='MP12_ULTRA_V1':bad.append('SCHEMA')
   rn=int(r.get('received_unix_ns') or 0);wn=int(r.get('writer_observed_unix_ns') or 0)
   if rn<=0 or wn<rn:bad.append('NS_TIMING')
   else:lat.append((wn-rn)/1000.)
   ex=reference(r);got=r.get('reference_post_price')
   if ex is not None and got is not None and abs(float(got)-ex)<=max(1e-15,abs(ex)*1e-10):refok+=1
   else:bad.append('REFERENCE')
 summary=read_json(ev/'summary.json',{}) or {};pools={x.get('symbol'):x.get('swaps') for x in summary.get('pools',[]) if isinstance(x,dict)}
 passed=(summary.get('minutes')==CAPTURE_MINUTES and engine_rc==0 and summary.get('session_complete') is True and summary.get('capture_target_reached') is True and summary.get('logging_failed') is False and summary.get('dropped_events')==0 and count==summary.get('swaps_total') and refok==count and not bad and n1_ok and set(pools)==set(POOLS) and summary.get('rpc_errors')==0 and summary.get('decode_errors')==0)
 def pc(a,q):
  if not a:return None
  a=sorted(a);return a[int((len(a)-1)*q)]
 return {'T3_TECHNICAL_PASS':passed,'requested_minutes':CAPTURE_MINUTES,'collector_minutes':summary.get('minutes'),'duration_matches_requested':summary.get('minutes')==CAPTURE_MINUTES,'engine_rc':engine_rc,'n1_control_ok':n1_ok,'swaps':count,'swaps_by_pool':dict(by),'summary_pools':pools,'reference_crosschecked':refok,'collector_to_writer_us_p50':pc(lat,.5),'collector_to_writer_us_p95':pc(lat,.95),'errors':bad[:20],'dropped_events':summary.get('dropped_events'),'rpc_errors':summary.get('rpc_errors'),'decode_errors':summary.get('decode_errors'),'paper_only':True,'signal_engine_feedback':False,'transactions_submitted':False}

def zip_result(ev,run,n2seg,d2run,out):
 config=read_json(ev/'POOL_PREFLIGHT.json',{}) or {}
 endpoints=[config.get('rpc',''),config.get('ws','')]
 if out.exists():raise RuntimeError('RESULT_EXISTS')
 with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for root,prefix in [(ev,'evidence/'+ev.name),(run,'t3_run')]+([(n2seg,'n2_mp12')] if n2seg else [])+([(d2run,'d2_ultra')] if d2run else []):
   if not root or not root.is_dir():continue
   for p in sorted(root.rglob('*')):
    if p.is_file() and not p.is_symlink():archive_file(z,p,prefix+'/'+p.relative_to(root).as_posix(),endpoints)
 return sha(out)

def discover_segment(ev):
 root=BASE/'runtime/r5d-n2-gpu-mp12/prospective';xs=sorted(root.glob(ev.name+'-segment-*'),key=lambda p:p.stat().st_mtime_ns) if root.is_dir() else []
 return xs[-1] if xs else None

def collector_command(ev, manifest):
 rpc,ws=connection_for_session(manifest)
 return [str(MP12/'bin/belial-r5c2-live-profiler'),'--minutes',str(CAPTURE_MINUTES),
         '--evidence',str(ev),'--state',str(ev/'dashboard_state.json'),'--rpc',rpc,'--ws',ws]

def run90m(pool_manifest,markets):
 selected_manifest=read_json(pool_manifest,{})
 selected_rpc,selected_ws=connection_for_session(selected_manifest)
 verify_manifest(selected_manifest)
 if os.geteuid()==0:raise RuntimeError('NAO_EXECUTE_COM_SUDO')
 if active_collectors():raise RuntimeError('ACTIVE_COLLECTOR_REFUSED:'+repr(active_collectors()))
 RT.mkdir(parents=True,exist_ok=True)
 lock=(RT/'runner.lock').open('a+')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise RuntimeError('T3_RUNNER_ALREADY_ACTIVE')
 old=old_states();runid='t3-90m-mp12-ultra-'+stamp();run=RT/'runs'/runid;run.mkdir(parents=True,exist_ok=False);tele=run/'telemetry.jsonl'
 atomic(run/'T3_START.json',{'schema':'belial_t3_90m_mp12_ultra_v1','minutes':CAPTURE_MINUTES,'duration_seconds':CAPTURE_SECONDS,'pools':POOLS,'old_components':old,'main_strategy_pnl':'FARTCOIN_ONLY_UNCHANGED','shadow_compare_pnl':'COUNTERFACTUAL_NOT_STRATEGY','microseconds':'LOCAL_PIPELINE_MEASUREMENT_NOT_SCREEN_REFRESH','paper_only':True,'signal_engine_feedback':False,'transactions_submitted':False})
 # Stop duplicate visual/GPU watchers only if they were actually alive; restore at the end.
 if old['d1']:control(OLD_D1,['--stop'])
 if old['n2']:control(OLD_N2,['--stop','--base',str(BASE)])
 n2p=n2logf=d2p=perpp=None;engine=None;bridge=None;logs=[];main_j=shadow_j=None;ev=None;rc=None;error=None
 try:
  sid='m2-mp12-t3-90m-'+stamp();ev=BASE/'evidence'/sid;ev.mkdir(parents=True,exist_ok=False);(BASE/'runtime/r5d-mp12').mkdir(parents=True,exist_ok=True);atomic(BASE/'runtime/r5d-mp12/active_session.json',{'evidence':str(ev),'runner_pid':os.getpid(),'started_ms':now_ms()})
  shutil.copy2(pool_manifest,ev/'POOL_PREFLIGHT.json');shutil.copy2(markets,ev/'PERP_MARKETS.json');os.environ['BELIAL_T3_POOL_MANIFEST']=str(ev/'POOL_PREFLIGHT.json')
  atomic(ev/'T3_RUN_MANIFEST.json',{'schema':'belial_t3_mp12_session_v1','minutes':CAPTURE_MINUTES,'duration_seconds':CAPTURE_SECONDS,'pools':list(POOLS),'roles':{'FARTCOIN':'MAIN_TAIL','BOME':'OBSERVE','WIF':'OBSERVE','POPCAT':'SHADOW_COMPARE','RAY':'SHADOW_CONTROL','JUP':'SHADOW_COMPARE','BONK':'SHADOW_COMPARE','PENGU':'SHADOW_COMPARE','PUMP':'SHADOW_COMPARE','USELESS':'SHADOW_COMPARE','GRASS':'SHADOW_COMPARE','PYTH':'SHADOW_COMPARE'},'visual_target_hz':25,'internal_ingest':'RUST_WEBSOCKET_BOUNDED_NO_SILENT_DROP','timestamp_precision':'UNIX_NS_LOCAL','paper_only':True,'orders':False,'transactions_submitted':False,'signal_engine_feedback':False})
  atomic(ev/'RPC_CONNECTION.json',{'connection':selected_manifest['rpc_connection'],'rpc':public_endpoint(selected_rpc),'ws':public_endpoint(selected_ws)})
  n2p,n2init,n2logf=start_n2(run/'n2_mp12.log',ev);atomic(run/'N2_INIT.json',n2init)
  elog=(ev/'engine-console.log').open('x');logs.append(elog)
  engine=subprocess.Popen(collector_command(ev,selected_manifest),stdin=subprocess.DEVNULL,stdout=elog,stderr=subprocess.STDOUT)
  deadline=time.time()+120
  while time.time()<deadline and not all((ev/n).exists() for n in ('swaps.jsonl','exits.jsonl')):
   if engine.poll() is not None:raise RuntimeError('MP12_ENGINE_START_FAIL:'+str(ev/'engine-console.log'))
   time.sleep(.05)
  if not all((ev/n).exists() for n in ('swaps.jsonl','exits.jsonl')):raise RuntimeError('MP12_METADATA_TIMEOUT')
  # imports only after release/evidence are fixed
  sys.path.insert(0,str(RUNREL/'tools'));sys.path.insert(1,str(MP12/'tools'))
  import common as mc
  from tail import Tail as MainTail
  from shadow_compare import Tail as ShadowTail
  main_j=mc.Journal(ev/'m2_tail');shadow_j=mc.Journal(ev/'shadow_compare');mt=MainTail(main_j.emit,sid);st=ShadowTail(shadow_j.emit,sid)
  readers={'swap':mc.Reader(ev/'swaps.jsonl'),'exit':mc.Reader(ev/'exits.jsonl')}
  blog=(ev/'n1-bridge.log').open('x');logs.append(blog);bridge=subprocess.Popen([sys.executable,'-B',str(MP12/'tools/n1_bridge.py'),'--evidence',str(ev),'--pid',str(engine.pid)],stdin=subprocess.DEVNULL,stdout=blog,stderr=subprocess.STDOUT)
  started_ns=time.time_ns();deadline_ns=capture_deadline_ns(started_ns)
  d2run=None
  plog=(ev/'perp-service.log').open('x');logs.append(plog)
  perpp=subprocess.Popen([sys.executable,'-B',str(PERP/'tools/serve.py'),'--evidence',str(ev),'--markets',str(ev/'PERP_MARKETS.json'),'--binary',str(MP12/'bin/belial-perp-feed'),'--deadline-ms',str(deadline_ns//1_000_000),'--duration-ms',str(CAPTURE_MS),'--n2-runtime',str(BASE/'runtime/r5d-n2-gpu-mp12'),'--n2-config',str(N2/'CONFIG_N2.json')],stdin=subprocess.DEVNULL,stdout=plog,stderr=subprocess.STDOUT,start_new_session=True)
  dashboard_opened=False
  prev_cpu=None;last_telem=0.;last_state=0.;last_print=0.;poolcounts=collections.Counter();startmono=time.monotonic()
  # 5ms timer resolution for Tail causal timers; data itself is event-driven in Rust/D2.
  while True:
   batch=[]
   for kind,r in readers.items():
    for row in r.read(2048):batch.append((int(row.get('writer_observed_unix_ns') or int(row.get('writer_observed_unix_ms') or 0)*1_000_000),kind,row))
   batch.sort(key=lambda x:x[0])
   at=mc.now();mt.advance(at,False);st.advance(at,False)
   for _,kind,row in batch:
    sym=str(row.get('symbol') or '')
    if kind=='swap':poolcounts[sym]+=1
    if sym in OLD_POOLS:mt.feed(kind,row,mc.now())
    st.feed(kind,row,mc.now())
   at=mc.now();mt.advance(at,True);st.advance(at,True)
   mono=time.monotonic()
   if batch or mono-last_state>=.10:
    mc.atomic(ev/'m2_tail/state.json',mt.state());mc.atomic(ev/'shadow_compare/state.json',st.state());last_state=mono
   if mono-last_telem>=1:
    prev_cpu,cpu=cpu_sample(prev_cpu);row={'unix_ms':now_ms(),'cpu_pct':cpu,'mem_available_gib':mem_gib(),'gpu':gpu(),'n2':read_json(BASE/'runtime/r5d-n2-gpu-mp12/status.json',{}) or {},'main_tail_counts':dict(mt.counts),'shadow_counts':dict(st.counts),'swaps_by_pool':dict(poolcounts)};append(tele,row);last_telem=mono
   if mono-last_print>=10:
    remain=max(0,int((deadline_ns-time.time_ns())/1e9));g=gpu();print(f'T3 | T-{format_countdown(remain)} | swaps={sum(poolcounts.values())} '+ ' '.join(f'{s}={poolcounts[s]}' for s in POOLS)+f' | MAIN {main_pnl_text(mt.state())} | GPU={g.get("util_pct","—")}% {g.get("temperature_c","—")}C',flush=True);last_print=mono
   if not dashboard_opened and (ev/'perp_t3/dashboard_url.txt').is_file():
    url=(ev/'perp_t3/dashboard_url.txt').read_text().strip();print('DASHBOARD_URL='+url,flush=True);dashboard_opened=True
    if shutil.which('xdg-open') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):subprocess.Popen(['xdg-open',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   if perpp.poll() is not None and perpp.returncode!=0:raise RuntimeError('PERP_SERVICE_FAILED:'+str(ev/'perp-service.log'))
   if n2p.poll() is not None:raise RuntimeError('N2_GPU_ENDED_EARLY')
   n2status=read_json(BASE/'runtime/r5d-n2-gpu-mp12/status.json',{}) or {}
   if n2status.get('state')=='N2_ERROR_M2_UNTOUCHED':raise RuntimeError('N2_OBSERVER_ERROR:'+str(n2status.get('error')))
   if bridge.poll() is not None and engine.poll() is None:raise RuntimeError('N1_BRIDGE_ENDED_EARLY')
   if STOP or (ev/'REQUEST_STOP').exists():raise RuntimeError('USER_STOP_REQUESTED')
   if engine.poll() is not None and not batch:break
   if watchdog_expired(mono-startmono):raise RuntimeError('T3_WALL_TIMEOUT')
   time.sleep(.005)
  rc=engine.wait();
  for r in readers.values():r.close()
  mt.advance(mc.now());st.advance(mc.now());main_state=mt.state();shadow_state=st.state();main_state['session_end_ms']=mc.now();shadow_state['session_end_ms']=mc.now();mc.atomic(ev/'m2_tail/state.json',main_state);mc.atomic(ev/'shadow_compare/state.json',shadow_state)
  main_j.close();main_j=None;shadow_j.close();shadow_j=None
  atomic(ev/'SESSION_COMPLETE',{'capture_process_ended':True,'engine_rc':rc,'error':None,'at_ms':now_ms()})
  try:bridge.wait(timeout=25)
  except subprocess.TimeoutExpired:bridge.terminate();bridge.wait(timeout=5)
  n1ok=bridge.returncode==0 and (ev/'n1_qualification/RESULT.json').is_file()
  validation=validate(ev,rc,n1ok);atomic(ev/'T3_VALIDATION.json',validation)
  # Wait for actual finalization, not merely a periodic summary file.
  for _ in range(160):
   state=read_json(BASE/'runtime/r5d-n2-gpu-mp12/status.json',{}) or {}
   if state.get('state')=='SESSION_FINISHED':break
   time.sleep(.125)
  else:raise RuntimeError('N2_FINALIZATION_TIMEOUT')
  try:perpp.wait(timeout=30)
  except subprocess.TimeoutExpired:raise RuntimeError('PERP_FINALIZATION_TIMEOUT')
  if perpp.returncode!=0:raise RuntimeError('PERP_FINALIZATION_FAILED')
  ps=read_json(ev/'perp_t3/SERVICE_COMPLETE.json',{}) or {}
  if not ps.get('ok'):raise RuntimeError('PERP_AUDIT_INCOMPLETE')
  validation['PERP_VALID_BOOK_OBSERVED']=any(int(n)>0 for n in ps.get('book_updates',{}).values())
  validation['PERP_BOOK_UPDATES']=ps.get('book_updates',{})
  validation['T3_TECHNICAL_PASS']=bool(validation['T3_TECHNICAL_PASS'] and validation['PERP_VALID_BOOK_OBSERVED'])
  atomic(ev/'T3_VALIDATION.json',validation)
  seg=discover_segment(ev);d2snap=read_json(ev/'perp_t3/SERVICE_COMPLETE.json',{}) or {};n2sum=read_json(seg/'summary.json',{}) if seg else {}
  # Refresh the archived dashboard after final N2 censoring, not a stale last live tick.
  final_state=read_json(ev/'perp_t3/state.json',{}) or {};final_books=read_json(ev/'perp_t3/books_final.json',{}) or {}
  final_state.update({'main':main_state,'shadow':shadow_state,'n2':n2sum or {},'complete':True})
  atomic(ev/'perp_t3/state.json',final_state)
  embedded='<script>window.BELIAL_FINAL_STATE='+json.dumps(final_state,ensure_ascii=False).replace('<','\\u003c')+';window.BELIAL_FINAL_BOOKS='+json.dumps(final_books,ensure_ascii=False).replace('<','\\u003c')+';</script>'
  (ev/'perp_t3/DASHBOARD_FINAL.html').write_text((PERP/'web/dashboard.html').read_text().replace('<!-- FINAL_STATE -->',embedded))
  report={'schema':'belial_t3_90m_final_v1','session':sid,'technical_validation':validation,'main_tail':main_state,'shadow_compare':shadow_state,'perp_paper':read_json(ev/'perp_t3/state.json',{}),'n2_summary':n2sum or {},'d2_ultra':d2snap,'swaps_by_pool':dict(poolcounts),'interpretation':{'main_pnl_only_fartcoin':True,'shadow_pool_pnl_counterfactual_not_strategy':True,'microsecond_metrics_local_pipeline_only':True,'visual_frame_rate_not_microseconds':True}}
  atomic(run/'T3_REPORT.json',report)
  out=DESKTOP/('BELIAL_R5D_T3_90M_MP12_ULTRA_RESULTADO_'+stamp()+'.zip');out.parent.mkdir(parents=True,exist_ok=True);zsha=zip_result(ev,run,seg,d2run,out)
  result={'BELIAL_T3_TECHNICAL_PASS':validation['T3_TECHNICAL_PASS'],'CAPTURE_MINUTES':CAPTURE_MINUTES,'CAPTURE_SECONDS':CAPTURE_SECONDS,'RESULT_ZIP':str(out),'RESULT_SHA256':zsha,'EVIDENCE':str(ev),'POOLS':list(POOLS),'N2_GPU_MP12':'8192x32','PERP_SERVICE':ps,'DASHBOARD_FINAL_HTML':str(ev/'perp_t3/DASHBOARD_FINAL.html'),'D2_EVENT_DRIVEN':True,'D2_VISUAL_TARGET_HZ':25,'MICROSECOND_METRICS':'LOCAL_PIPELINE_ONLY','MAIN_STRATEGY_MODIFIED':False,'FROZEN_RULES_MODIFIED':False,'SIGNAL_ENGINE_FEEDBACK':False,'TRANSACTIONS_SUBMITTED':False}
  atomic(run/'RESULT.json',result);print(json.dumps(result,indent=2,ensure_ascii=False),flush=True);return 0 if validation['T3_TECHNICAL_PASS'] else 2
 except BaseException as e:
  error=str(e)
  if ev:atomic(ev/'T3_FAILURE.json',{'error':error,'at_ms':now_ms(),'orders':False,'evidence_preserved':True})
  raise
 finally:
  if ev and engine and engine.poll() is None:
   try:(ev/'REQUEST_STOP').write_text('stop\n')
   except Exception:pass
   engine.terminate()
   try:engine.wait(timeout=8)
   except Exception:engine.kill();engine.wait()
  if ev and not (ev/'SESSION_COMPLETE').exists():
   try:atomic(ev/'SESSION_COMPLETE',{'capture_process_ended':True,'engine_rc':engine.returncode if engine else None,'error':error,'at_ms':now_ms()})
   except Exception:pass
  if bridge and bridge.poll() is None:
   bridge.terminate()
   try:bridge.wait(timeout=5)
   except Exception:bridge.kill()
  if perpp and perpp.poll() is None:
   perpp.terminate()
   try:perpp.wait(timeout=20)
   except Exception:perpp.kill();perpp.wait()
  if d2p and d2p.poll() is None:
   d2p.terminate()
   try:d2p.wait(timeout=4)
   except Exception:d2p.kill()
  try:stop_n2(n2p)
  except Exception:pass
  if n2logf:
   try:n2logf.close()
   except Exception:pass
  if main_j:
   try:main_j.close()
   except Exception:pass
  if shadow_j:
   try:shadow_j.close()
   except Exception:pass
  for f in logs:
   try:f.close()
   except Exception:pass
  if error and ev:
   try:
    DESKTOP.mkdir(parents=True,exist_ok=True);out=DESKTOP/('BELIAL_R5D_T3_90M_FIX4_FALHA_'+stamp()+'.zip');zip_result(ev,run,discover_segment(ev),None,out);print('FAILURE_ZIP='+str(out),flush=True)
   except Exception as e:print('FAILURE_ARCHIVE_ERROR='+str(e),file=sys.stderr)
  # Restore exactly the old watchers that were running before T3.
  try:
   if old.get('n2'):control(OLD_N2,['--start','--base',str(BASE)],40)
  except Exception as e:print('WARN_RESTORE_OLD_N2='+str(e),file=sys.stderr)
  try:
   if old.get('d1'):control(OLD_D1,['--start','--max-hz','120'],20)
  except Exception as e:print('WARN_RESTORE_OLD_D1='+str(e),file=sys.stderr)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--minutes',type=int,default=CAPTURE_MINUTES);ap.add_argument('--pool-manifest',type=Path,required=True);ap.add_argument('--markets',type=Path,required=True);a=ap.parse_args()
 if a.minutes!=CAPTURE_MINUTES:ap.error('T3 90M FIX4 frozen at exactly 90 minutes (1h30)')
 signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop);return run90m(a.pool_manifest,a.markets)
if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as e:print('BELIAL_T3_FAIL='+str(e),flush=True);raise
