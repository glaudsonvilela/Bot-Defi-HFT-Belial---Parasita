"""Local A4 supervisor. Preparation faults become component states, not a global MP12 gate."""
from __future__ import annotations
import argparse,datetime,fcntl,shutil,webbrowser,zipfile
from a4.common import *
from a4.catalog import initial,freeze,summarize
from a4.book_health import classify_book

def observation_available(session, cohort):
    """Do not label an empty preparation as a 90-minute market experiment."""
    session = Path(session)
    if cohort.get('qualified_at_start'):
        return True
    books = read_json(session/'paper/books.json', {}) or {}
    if now_ms() - books.get('at_ms', 0) <= 2000 and any(b.get('ready') is True for b in books.get('books', [])):
        return True
    counts = (read_json(session/'raw/status.json', {}) or {}).get('raw_events', {})
    return any(isinstance(n, int) and n > 0 for n in counts.values())

class Supervisor:
    def __init__(self,session,base=BASE):
        self.session=Path(session);self.base=Path(base);self.procs={};self.logs={};self.stop=Stop().install();self.phase='PREPARING';self.stage='Inicializando';self.opened_url=None;self.ui_restarts=0;self.failures={};self.samples={s:dict(qualified_ms=0,ready_ms=0,book_ready_ms=0) for s in SYMBOLS};self.last_sample=None
    def launch(self,name,module,*args):
        if name in self.procs:raise RuntimeError('COMPONENT_ALREADY_CREATED:'+name)
        p,f=spawn(command(module,'--session',self.session,*args),self.session/'logs'/(name+'.log'))
        self.procs[name]=p;self.logs[name]=f;return p
    def component_states(self):
        mapping={'build':'build','paper':'paper','spot_prepare':'spot_prep','raw':'raw','analysis':'aggregate','n2':'n2','rpc_relay':'rpc_relay','price_samples':'price_samples','ws_relay':'ws_relay','ui':'ui'}
        states={}
        for name,p in self.procs.items():
            folder=mapping.get(name,'sources/'+name[7:] if name.startswith('sensor_') else name)
            row=read_json(self.session/folder/'status.json',{}) or {};row=dict(row)
            if p.poll() is not None:
                complete=read_json(self.session/folder/'COMPLETE.json',{}) or {}
                row.update(process_alive=False,returncode=p.returncode)
                if p.returncode!=0 or complete.get('ok') is False:row['state']='FAILED'
                elif row.get('state') not in ('SESSION_FINISHED','FINISHED','COMPLETE','STOPPED'):row['state']='FINISHED'
            else:row.update(process_alive=True,heartbeat_age_ms=max(0,now_ms()-row.get('at_ms',now_ms())))
            states[name]=row or {'state':'STARTING'}
        return states
    def publish(self):
        t=now_ms();cohort=read_json(self.session/'COHORT.json')
        statuses={s:read_json(self.session/'sources'/s/'status.json',{}) or {} for s in SYMBOLS}
        analysis=read_json(self.session/'aggregate/status.json',{}) or {};bad=read_json(self.session/'ANALYSIS_BLOCKED.json',{}) or {}
        if self.phase=='CAPTURING':
            for s in statuses:
                if s in bad or analysis.get('state')!='ACTIVE' or t-analysis.get('at_ms',0)>5000:statuses[s]=dict(statuses[s],state='QUARANTINED',reason=bad.get(s,'ANALYSIS_NOT_ACTIVE'),integrity=False)
        integrity=read_json(self.session/'INTEGRITY.json',{'ok':True})
        books=read_json(self.session/'paper/books.json',{}) or {};book_rows=books.get('books',[])
        # Execution freshness remains the inherited 2 s safety gate. A4 adds a
        # DISPLAY state so an old instrument is not mislabeled as a dead socket.
        elapsed=max(0,t-books.get('at_ms',t));venue_runtime=books.get('venue_runtime',{}) or {}
        for row in book_rows:
            transport=(venue_runtime.get(row.get('venue')) or {}).get('display_transport') or (books.get('transport') or {}).get(row.get('venue')) or 'UNKNOWN'
            display=classify_book(row,transport,elapsed,2000);row.update(display)
            if not display['ready_for_fills']:row['ready']=False
        coverage=summarize(cohort,statuses,book_rows,t,integrity.get('ok') is True)
        if cohort is None:
            caps=read_json(self.session/'spot_prep/CAPABILITIES.json',{}) or initial()
            for row in coverage['rows']:row.update(caps.get(row['symbol'],{}))
        if self.phase=='CAPTURING':
            mono=time.monotonic_ns();dt=0 if self.last_sample is None else min(2000,(mono-self.last_sample)//1_000_000);self.last_sample=mono
            for row in coverage['rows']:
                s=row['symbol']
                if row.get('qualified'):self.samples[s]['qualified_ms']+=dt
                if row['signal_source_ready']:self.samples[s]['ready_ms']+=dt
                if row['venues_ready']:self.samples[s]['book_ready_ms']+=dt
        label='Ainda não congelado' if cohort is None else '24 leitores qualificados · cobertura temporal em medição' if cohort['integral_mp24_at_start'] else f"COBERTURA PARCIAL · {len(cohort['qualified_at_start'])}/24 leitores qualificados"
        platform={'schema':'belial_a4_platform_v1','at_ms':t,'phase':self.phase,'stage':self.stage,'coverage':coverage,'experiment_label':label,'components':self.component_states(),
                  'raw_counts':read_json(self.session/'raw/status.json',{}).get('raw_events',{}),'integrity':integrity,'paper_stale':t-read_json(self.session/'paper/status.json',{}).get('at_ms',0)>3000,'failures':self.failures,'orders':False}
        atomic(self.session/'PLATFORM.json',platform)
        ui=read_json(self.session/'ui/URL.json',{})
        if ui.get('url') and ui['url']!=self.opened_url:
            self.opened_url=ui['url'];print('DASHBOARD_URL='+self.opened_url,flush=True)
            if not os.environ.get('BELIAL_A1_NO_BROWSER') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
                def open_page(url):
                    with contextlib.suppress(Exception):subprocess.run(['xdg-open',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
                __import__('threading').Thread(target=open_page,args=(self.opened_url,),daemon=True).start()
        if 'ui' in self.procs and self.procs['ui'].poll() is not None and self.phase not in ('FINISHED','STOPPING') and self.ui_restarts<3:
            self.logs['ui'].close();del self.procs['ui'];self.ui_restarts+=1;self.launch('ui','ui_worker')
        return platform
    def setup(self):
        for d in ('logs','paper','aggregate','sources','ui','n2','build','price_samples','ws_relay'): (self.session/d).mkdir(exist_ok=True)
        for name in ('swaps.jsonl','exits.jsonl'):(self.session/'aggregate'/name).touch(exist_ok=False)
        # Channel ownership exists before CAPTURING; the producer still proves its own readiness.
        (self.session/'price_samples/observations.jsonl').touch(exist_ok=False)
        atomic(self.session/'n2/INPUT_CONTRACT.json',{'schema':'a4_agent_inputs_v1','channel_created_ms':now_ms(),
            'producer_ready':False,'producer_file':'price_samples/READY.json','no_fake_observations':True},True)
        atomic(self.session/'SESSION.json',{'schema':'belial_a4_session_v1','phase':'PREPARING','duration_ms':DURATION_MS,'minutes':MINUTES,'created_ms':now_ms(),'orders':False,'requested':SYMBOLS},durable=True)
        atomic(self.session/'INTEGRITY.json',{'ok':True});atomic(self.session/'CONFIG_SCIENCE_SHA256.json',read_json(ROOT/'LEGACY_SHA256.json'),durable=True)
        from a4.economics import validate_costs
        path=Path(os.environ['BELIAL_A4_COSTS_FILE']).expanduser() if os.environ.get('BELIAL_A4_COSTS_FILE') else ROOT/'CONFIG_CUSTOS_EXTERNOS.json'
        safe(path)
        if path.stat().st_size>65536:raise ValueError('COST_PROFILE_TOO_LARGE')
        costs=validate_costs(read_json(path));atomic(self.session/'COSTS_FROZEN.json',costs,durable=True)
        atomic(self.session/'UNIVERSE_FROZEN.json',read_json(ROOT/'UNIVERSE_A2.json'),durable=True)
        self.launch('ui','ui_worker');self.publish()
    def prepare(self):
        self.launch('build','build_worker','--target',self.base/'target-belial-a4')
        self.launch('n2','n2_worker');prep_started=None;paper_started=None;last_progress=0
        while not self.stop.value:
            build=read_json(self.session/'build/RESULTS.json',{}) or {};done=(self.session/'build/DONE.json').exists()
            if 'perp' in build and 'paper' not in self.procs:
                binary=Path(build['perp'].get('path') or self.session/'BINARY_UNAVAILABLE')
                self.launch('paper','paper_worker','--binary',binary);paper_started=time.monotonic()
            if 'spot' in build and 'spot_prepare' not in self.procs:
                self.launch('spot_prepare','spot_prepare','--binary',build['spot'].get('path') or self.session/'BINARY_UNAVAILABLE');prep_started=time.monotonic()
            if (self.session/'spot_prep/RAW_SOURCES.json').exists() and build.get('raw',{}).get('ok') and 'raw' not in self.procs:
                self.launch('raw','raw_worker','--binary',build['raw']['path'])
            prep_done=(self.session/'spot_prep/DONE.json').exists()
            if prep_started and not prep_done and time.monotonic()-prep_started>900:
                terminate(self.procs['spot_prepare'],8);self.failures['spot_prepare']='PREPARATION_BUDGET_900S_REACHED';prep_done=True
            if self.procs['build'].poll() is not None and not done:
                self.failures['build']='BUILD_WORKER_FAILED';done=True
                if 'paper' not in self.procs:self.launch('paper','paper_worker','--binary',self.session/'BINARY_UNAVAILABLE');paper_started=time.monotonic()
                if 'spot_prepare' not in self.procs:prep_done=True
            paper_ready=read_json(self.session/'paper/status.json',{}).get('metadata_finished') or (paper_started and time.monotonic()-paper_started>45)
            if 'paper' in self.procs and self.procs['paper'].poll() is not None:paper_ready=True
            self.stage=read_json(self.session/'build/status.json',{}).get('component','Preparação')+' · fontes independentes'
            self.publish()
            if now_ms()-last_progress>5000:
                b=read_json(self.session/'build/status.json',{}) or {};spot=read_json(self.session/'spot_prep/status.json',{}) or {}
                print('A4 PREPARAÇÃO | build='+str(b.get('component'))+' '+str(b.get('command'))+' '+str(b.get('state'))+' | '+str(b.get('elapsed_seconds','?'))+'s | spot='+str(spot.get('state'))+' | qualificados='+str(spot.get('qualified',0))+'/24',flush=True)
                last_progress=now_ms()
            if done and prep_done and paper_ready:break
            time.sleep(.25)
        if self.stop.value:return False
        capabilities=read_json(self.session/'spot_prep/CAPABILITIES.json',{}) or initial();cohort=freeze(capabilities,now_ms());atomic(self.session/'COHORT.json',cohort,durable=True)
        if not observation_available(self.session,cohort):
            self.failures['preparation']='NO_USABLE_MARKET_SOURCE_CAPTURE_NOT_STARTED'
            self.stage='Sem fonte utilizável; painel e diagnóstico preservados; captura não iniciada'
            self.publish();return False
        connection=read_json(self.session/'spot_prep/CONNECTION.json')
        if connection and cohort['qualified_at_start']:
            self.launch('rpc_relay','rpc_relay');deadline=time.monotonic()+10
            while not (self.session/'rpc_relay/ENDPOINT.json').exists() and time.monotonic()<deadline and not self.stop.value:self.publish();time.sleep(.1)
            self.launch('ws_relay','ws_relay');limit=time.monotonic()+10
            while not (self.session/'ws_relay/ENDPOINT.json').exists() and time.monotonic()<limit and not self.stop.value:self.publish();time.sleep(.1)
        state=session_state(self.session);now=now_ms();state.update(phase='CAPTURING',started_ms=now,deadline_ms=now+DURATION_MS,started_monotonic_ns=time.monotonic_ns(),deadline_monotonic_ns=time.monotonic_ns()+DURATION_MS*1_000_000)
        atomic(self.session/'SESSION.json',state,durable=True);self.phase='CAPTURING';self.stage='90 minutos · sessão nova · PAPER/shadow'
        self.launch('analysis','analysis_worker')
        if (self.session/'rpc_relay/ENDPOINT.json').exists():self.launch('price_samples','price_samples')
        build=read_json(self.session/'build/RESULTS.json',{}) or {}
        for s in SYMBOLS:
            if s in cohort['qualified_at_start'] and (self.session/'rpc_relay/ENDPOINT.json').exists() and (self.session/'ws_relay/ENDPOINT.json').exists():
                self.launch('sensor_'+s,'sensor_worker','--symbol',s,'--binary',build['spot']['path'])
            else:heartbeat(self.session/'sources'/s/'status.json','QUARANTINED',reason=capabilities.get(s,{}).get('reason','NOT_QUALIFIED_OR_RELAY_UNAVAILABLE'),generation=0,integrity=False)
        print('BELIAL_A4_CAPTURE_STARTED=true; MINUTES=90; REQUESTED=24; QUALIFIED='+str(len(cohort['qualified_at_start']))+'; ORDERS=false',flush=True)
        print('SESSION='+str(self.session),flush=True);return True
    def capture(self):
        last_print=0
        while not self.stop.value and not session_expired(self.session):
            clock=session_state(self.session)
            drift=(now_ms()-clock['started_ms'])-(time.monotonic_ns()-clock['started_monotonic_ns'])/1_000_000
            if abs(drift)>2000:atomic(self.session/'INTEGRITY.json',{'ok':False,'reason':'WALL_CLOCK_DRIFT','drift_ms':drift})
            for name,p in self.procs.items():
                if name in ('analysis','paper','rpc_relay','ws_relay','n2') and p.poll() is not None and name not in self.failures:
                    self.failures[name]='PROCESS_ENDED:'+str(p.returncode)
                    if name in ('analysis','paper'):atomic(self.session/'INTEGRITY.json',{'ok':False,'reason':name.upper()+'_PROCESS_ENDED_NO_POSITION_RESET'})
            if shutil.disk_usage(self.session).free<250*1024*1024:
                atomic(self.session/'INTEGRITY.json',{'ok':False,'reason':'DISK_LOW_NEW_ENTRIES_DISABLED'});self.failures['disk']='LOW_SPACE'
            self.publish()
            if now_ms()-last_print>15000:
                state=session_state(self.session);left=max(0,(state['deadline_monotonic_ns']-time.monotonic_ns())//1_000_000_000)
                print(f'A4 | restante={left//3600:02}:{left%3600//60:02}:{left%60:02} | {self.stage}',flush=True);last_print=now_ms()
            time.sleep(.2)
    def finish(self):
        print('A4 FINALIZAÇÃO 1/4 | Sem novas entradas; encerrando fontes e reconciliando funding.',flush=True)
        self.phase='STOPPING';self.stage='Encerramento e reconciliação';state=session_state(self.session);state.update(phase='STOPPING',interrupted=self.stop.value);atomic(self.session/'SESSION.json',state,durable=True)
        for s in SYMBOLS:
            native=self.session/'sources'/s/('m2-'+s)
            if native.is_dir():(native/'REQUEST_STOP').touch()
        deadline=time.monotonic()+110;last_print=0
        while any(p.poll() is None for name,p in self.procs.items() if name!='ui') and time.monotonic()<deadline:
            self.publish()
            if time.monotonic()-last_print>5:
                running=[n for n,p in self.procs.items() if n!='ui' and p.poll() is None]
                print('A4 ENCERRANDO | '+', '.join(running)+' | limite restante '+str(round(max(0,deadline-time.monotonic())))+'s',flush=True);last_print=time.monotonic()
            time.sleep(.2)
        for name,p in self.procs.items():
            if name=='ui':continue
            if p.poll() is None:terminate(p,5);self.failures[name]='STOP_TIMEOUT'
        (self.session/'aggregate/SESSION_COMPLETE').touch()
        self.phase='STOPPING';self.stage='Replay de integridade · sem novas operações'
        self.publish()
        print('A4 FINALIZAÇÃO 2/4 | Replay de integridade; nenhum trade novo.',flush=True)
        replay_result={'ok':False,'reason':'PAPER_LEDGER_NOT_AVAILABLE'}
        if (self.session/'paper/COMPLETE.json').exists():
            try:
                from a4.replay import replay
                import threading
                pulse_done=threading.Event()
                def pulse():
                    started=time.monotonic()
                    while not pulse_done.wait(5):print('A4 REPLAY | em andamento '+str(round(time.monotonic()-started))+'s · registros preservados',flush=True)
                pulse_thread=threading.Thread(target=pulse,daemon=True);pulse_thread.start()
                try:replay_result=replay(self.session/'paper/inputs.jsonl',self.session/'paper_replay')
                finally:pulse_done.set();pulse_thread.join(1)
                replay_result['output_matches_live']=replay_result['output_sha256']==digest(self.session/'paper/outputs.jsonl')
                if not replay_result['output_matches_live']:atomic(self.session/'INTEGRITY.json',{'ok':False,'reason':'PAPER_REPLAY_OUTPUT_MISMATCH'})
            except Exception as e:replay_result={'ok':False,'error':str(e)};atomic(self.session/'INTEGRITY.json',{'ok':False,'reason':'REPLAY_FAILED'})
        atomic(self.session/'REPLAY_CHECK.json',replay_result)
        print('A4 FINALIZAÇÃO 3/4 | Relatório, agente e terminal final.',flush=True)
        self.phase='FINISHED';self.stage='Sessão encerrada · relatório e compactação'
        state.update(phase='FINISHED',ended_ms=now_ms());atomic(self.session/'SESSION.json',state,durable=True)
        for relative in ('paper/state.json','paper/books.json'):
            old=read_json(self.session/relative,{}) or {};old['complete']=True;atomic(self.session/relative,old)
        platform=self.publish();paper=read_json(self.session/'paper/state.json',{}) or {};book=read_json(self.session/'paper/books.json',{}) or {}
        # Supervisor sampling is an estimate; not a provider-certified continuous-data metric.
        summary={'schema':'belial_a4_result_v1','minutes_requested':90,'requested_assets':list(SYMBOLS),'cohort':read_json(self.session/'COHORT.json'),
                 'coverage_sampled_ms':self.samples,'coverage_is_supervisor_sampled':True,'failures':self.failures,'replay':replay_result,
                 'integrity':read_json(self.session/'INTEGRITY.json'),'interrupted':self.stop.value,'orders':False,
                 'experiment_integral_certified':False,'experiment_note':'Use coverage and integrity. Qualified adapters alone do not certify 24/24 continuous market coverage.',
                 'capture_started':bool(state.get('started_ms')),'capture_elapsed_ms':min(DURATION_MS,max(0,(time.monotonic_ns()-state['started_monotonic_ns'])//1_000_000)) if state.get('started_monotonic_ns') else 0,
                 'paper_is_not_main_strategy_pnl':True,'native_build':read_json(self.session/'build/RESULTS.json',{}),'n2':read_json(self.session/'n2/status.json',{}),'agent_validation':read_json(self.session/'n2/AGENT_VALIDATION.json',{}),'agent_advantage_proven':False}
        atomic(self.session/'SUMMARY.json',summary,durable=True)
        html=(ROOT/'a4/web/dashboard.html').read_text();embed=''
        for name,obj in (('STATE',paper),('BOOKS',book),('PLATFORM',platform),('AGENT',read_json(self.session/'n2/AGENT_VIEW.json',{}) or {}),('AGENT_STATUS',read_json(self.session/'n2/status.json',{}) or {})):embed+='window.BELIAL_FINAL_'+name+'='+canonical(obj).replace('<','\\u003c')+';'
        html=html.replace('</head>','<script>'+embed+'</script></head>');(self.session/'DASHBOARD_FINAL.html').write_text(html)
        (self.session/'UI_STOP').touch();time.sleep(.15)
        for name,p in self.procs.items():terminate(p,5)
        for log in self.logs.values():log.close()
        return summary

def package_result(session,output):
    add_legacy('runner');from rpc_transport import archive_file
    connection=read_json(Path(session)/'spot_prep/CONNECTION.json',{}) or {};endpoints=[connection.get('rpc'),connection.get('ws')]
    print('A4 FINALIZAÇÃO 4/4 | Compactando evidências e verificando hashes.',flush=True)
    output=Path(output);output.mkdir(parents=True,exist_ok=True);name='BELIAL_A4_90M_RESULTADO_'+Path(session).name.removeprefix('m2-a4-')+'.zip';path=output/name
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED,compresslevel=4) as z:
        files=sorted(Path(session).rglob('*'));last_print=0
        for i,file in enumerate(files,1):
            safe(file)
            if file.is_file():archive_file(z,file,Path(session).name+'/'+str(file.relative_to(session)),endpoints)
            if time.monotonic()-last_print>5:print(f'A4 ZIP | arquivos {i}/{len(files)}',flush=True);last_print=time.monotonic()
        # Inventory after redaction: exported hashes correspond to exported bytes.
        inv={}
        for index,info in enumerate(z.infolist(),1):
            h=__import__('hashlib').sha256()
            with z.open(info) as f:
                for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
            inv[info.filename]=h.hexdigest()
            if time.monotonic()-last_print>5:print(f'A4 ZIP | hashes {index}/{len(z.infolist())}',flush=True);last_print=time.monotonic()
        z.writestr('ZIP_SHA256.json',canonical(inv))
    print('RESULT_ZIP='+str(path),flush=True);print('FINAL_DASHBOARD='+str(Path(session)/'DASHBOARD_FINAL.html'),flush=True);return path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path);a=ap.parse_args()
    if os.geteuid()==0:raise SystemExit('Execute sem sudo/root.')
    safe(BASE)
    if not Path('/mnt/sherlock-ssd').is_mount():raise SystemExit('SSD não está montado. Nenhuma captura foi iniciada.')
    BASE.mkdir(parents=True,exist_ok=True);runtime=BASE/'runtime/belial-a4';runtime.mkdir(parents=True,exist_ok=True)
    lock=(runtime/'supervisor.lock').open('a+');fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    # Refuse concurrent legacy collector; never stop it.
    current_pid=os.getpid()
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            # Do not classify this supervisor itself as a concurrent collector.
            if int(proc.name)==current_pid:continue
            raw=(proc/'cmdline').read_bytes()
            if (b'belial-r5c2-live-profiler' in raw and str(BASE).encode() in raw) or b'a1.supervisor' in raw or b'a2.supervisor' in raw or b'a3.supervisor' in raw or b'a4.supervisor' in raw:raise SystemExit('Há um coletor Belial ativo. Ele foi preservado; A4 não iniciou outra captura.')
        except (FileNotFoundError,PermissionError,ValueError):pass
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');session=a.session or BASE/'evidence'/('m2-a4-'+stamp)
    if session.exists():raise SystemExit('A4 exige uma pasta de sessão nova; não reinicia contabilidade anterior.')
    if not str(session).startswith(str(BASE/'evidence')+'/'):raise SystemExit('Pasta de evidência fora da base.')
    safe(session);session.mkdir(parents=True);supervisor=Supervisor(session)
    try:
        supervisor.setup()
        if supervisor.prepare():supervisor.capture()
    except Exception as e:
        supervisor.failures['supervisor']=str(e);print('A4_SUPERVISOR_ERROR='+str(e),flush=True)
    finally:
        try:supervisor.finish()
        except Exception as e:
            atomic(session/'FINALIZATION_ERROR.json',{'error':str(e)})
            for p in supervisor.procs.values():terminate(p,5)
        package_result(session,Path.home()/'Área de trabalho/Altside')
    summary=read_json(session/'SUMMARY.json',{}) or {}
    return 0 if summary.get('capture_started') and not supervisor.failures.get('supervisor') and summary.get('integrity',{}).get('ok') is True else 2
if __name__=='__main__':raise SystemExit(main())
