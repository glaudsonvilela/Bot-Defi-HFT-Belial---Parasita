"""A6 isolated two-hour run, with automatic wait for the existing experiment."""
import argparse,contextlib,datetime,fcntl,json,os,shutil,subprocess,sys,time,webbrowser
from pathlib import Path
from a51.common import BASE,ROOT,Stop,atomic,read_json,now_ms,spawn,terminate,clean_env
from a51.supervisor import Supervisor,package_result

def active_legacy():
    # Match modules/native executables, not shell strings mentioning these names.
    found=[]
    for p in Path('/proc').glob('[0-9]*'):
        try:
            args=[a.decode(errors='replace') for a in (p/'cmdline').read_bytes().split(b'\0') if a]
            modules={'a1.supervisor','a2.supervisor','a3.supervisor','a4.supervisor','a5.supervisor','a51.supervisor','a51.resume_guard','a51.run_manager'}
            hit=any(a in modules for a in args) or (args and Path(args[0]).name=='belial-r5c2-live-profiler' and any(str(BASE) in a for a in args))
            if hit:found.append(int(p.name))
        except (OSError,ValueError):pass
    return found
class A6Supervisor(Supervisor):
    def launch(self,name,module,*args):
        if module not in ('wide_worker','discovery','wide_ui'):return super().launch(name,module,*args)
        p,f=spawn([sys.executable,'-B','-m','a6.'+module,'--session',str(self.session),*map(str,args)],self.session/'logs'/(name+'.log'));self.procs[name]=p;self.logs[name]=f;return p
    def setup(self):
        super().setup();self.launch('wide','wide_worker');self.launch('discovery','discovery');self.launch('wide_ui','wide_ui')
    def prepare(self):
        ok=super().prepare()
        if not (read_json(self.session/'build/RESULTS.json',{}) or {}).get('wide',{}).get('ok'):
            self.failures['wide']='NATIVE_GATE_FAILED';return False
        return ok
    def publish(self):
        p=super().publish()
        if time.monotonic()-getattr(self,'last_resource_sample',0)>=5:
            from a6.resources import sample
            try:sample(self.session)
            except OSError as exc:self.failures['resource_sampling']=str(exc)
            self.last_resource_sample=time.monotonic()
        url=(read_json(self.session/'wide_ui/URL.json',{}) or {}).get('url')
        if url and url!=getattr(self,'wide_url_printed',None):
            self.wide_url_printed=url;print('A6_SCANNER_URL='+url,flush=True)
        for name in ('wide','discovery'):
            if name in self.procs and self.procs[name].poll() is not None and self.phase=='CAPTURING':self.failures[name]='PROCESS_ENDED:'+str(self.procs[name].returncode)
        return p
    def finish(self):
        # The auxiliary web server terminates with SESSION phase; inherited UI remains for final report.
        super().finish()

def run(output):
    if os.geteuid()==0:raise RuntimeError('EXECUTE_SEM_SUDO')
    if not Path('/mnt/sherlock-ssd').is_mount():raise RuntimeError('SSD_NOT_MOUNTED')
    runtime=BASE/'runtime/belial-a6';runtime.mkdir(parents=True,exist_ok=True);lock=(runtime/'run.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stop=Stop().install();last=0
    while active_legacy():
        if stop.value:return 130
        if time.monotonic()-last>30:
            print('A6_AGUARDANDO_SCAN_ANTERIOR=true; captura de 2h ainda não começou; dados anteriores preservados',flush=True);last=time.monotonic()
        time.sleep(1)
    if shutil.disk_usage(BASE).free<6*1024**3:raise RuntimeError('SPACE_REQUIRED_6_GIB_FOR_BUILD_AND_EVIDENCE')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');session=BASE/'evidence'/('m2-a6-'+stamp);session.mkdir(parents=True)
    atomic(runtime/'ACTIVE_RUN.json',dict(session=str(session),release=str(ROOT),started_ms=now_ms(),orders=False),True)
    supervisor=A6Supervisor(session)
    try:
        supervisor.setup()
        print('A6_GPU_GATE=EXECUTING_ON_LOCAL_DEVICE',flush=True)
        with (session/'GPU_VALIDATION.json').open('w') as log:
            subprocess.run([sys.executable,'-B','-m','a6.gpu_check'],cwd=ROOT,env=clean_env(),stdout=log,stderr=subprocess.STDOUT,timeout=120,check=True)
        print('A6_GPU_GATE=PASS',flush=True)
        if supervisor.prepare():supervisor.capture()
    except Exception as exc:supervisor.failures['supervisor']=str(exc);print('A6_ERROR='+str(exc),flush=True)
    finally:
        try:supervisor.finish()
        except Exception as exc:atomic(session/'FINALIZATION_ERROR.json',dict(error=str(exc)))
        for p in supervisor.procs.values():terminate(p,5)
        wide=read_json(session/'wide/state.json',{}) or {}
        if not wide.get('catalog') or not wide.get('counts',{}).get('wide_prices'):supervisor.failures['wide_coverage']='NO_VERIFIED_BINANCE_PUBLIC_COVERAGE'
        for metric in ('books','flow_buckets'):
            if not wide.get('counts',{}).get(metric):supervisor.failures['wide_'+metric]='NO_OBSERVED_DEEP_DATA'
        dex=read_json(session/'discovery/state.json',{}) or {}
        atomic(session/'A6_SUMMARY.json',dict(orders=False,wide={k:v for k,v in wide.items() if k!='leaders'},discovery={k:v for k,v in dex.items() if k!='rows'},failures=supervisor.failures,api_capture_validation='CAPTURED_NOT_PROFIT_VALIDATION' if wide.get('counts',{}).get('wide_prices') else 'NO_LIVE_COVERAGE',duration_requested_ms=7200000),True)
        from a6.wide_ui import final
        final(session)
        result=package_result(session,output);print('BELIAL_A6_RESULTADO='+str(result),flush=True)
        atomic(runtime/'LAST_RESULT.json',dict(zip=str(result),session=str(session),orders=False),True)
    return 0 if not supervisor.failures else 2
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=Path('/home/hobit/Downloads'));a=p.parse_args();raise SystemExit(run(a.output))
