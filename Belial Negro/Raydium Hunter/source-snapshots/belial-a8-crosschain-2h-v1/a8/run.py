"""Isolated two-hour PAPER supervisor. All trading endpoints are absent."""
import argparse,collections,datetime,fcntl,gzip,json,os,queue,shutil,subprocess,sys,threading,time,zipfile
from pathlib import Path
from a51.common import BASE,ROOT,Stop,atomic,now_ms,canonical,clean_env,terminate,digest,read_json
from a51.journal import Journal
from a8.net import RPC,audit_writer,error_label
from a8.selection import prepare
from a8.chains import Sensors
from a8.lab import Lab
from a8.ui import UI,final_html
from a8.readiness import BNB_DEFAULT_RPC,check_bnb_capabilities,snapshot as readiness,capture_readiness
DURATION_MS=7200000
FOLLOWUP_MS=3600000
PREPARATION_MS=120000
CHAIN_STABLE_MS=60000
READINESS_TIMEOUT_SECONDS=360
from a8.network_identity import check_solana_mainnet

def active_scans():
    found=[];modules={'a1.supervisor','a2.supervisor','a3.supervisor','a4.supervisor','a5.supervisor','a51.supervisor','a51.run_manager','a51.resume_guard','a6.run','a7.run','a8.run'}
    for p in Path('/proc').glob('[0-9]*'):
        try:
            if int(p.name)==os.getpid() or p.stat().st_uid!=os.getuid():continue
            args=[r.decode(errors='replace') for r in (p/'cmdline').read_bytes().split(b'\0') if r]
            if any(r in modules for r in args) or (args and Path(args[0]).name in ('belial-wide-feed','belial-a8-feed','belial-r5c2-live-profiler','belial-spot-raw') and any(str(BASE) in r for r in args)):
                found.append(int(p.name))
        except (OSError,ValueError):pass
    return found

def native_build(session,target,stop,ui):
    out=session/'build';out.mkdir();results={};env=clean_env();env.update(CARGO_TARGET_DIR=str(target),CARGO_NET_OFFLINE='true')
    available=next((int(r.split()[1])*1024 for r in Path('/proc/meminfo').read_text().splitlines() if r.startswith('MemAvailable:')),2*1024**3)
    jobs=max(1,min(os.cpu_count() or 1,int(available//(1536*1024**2))))
    for name in ('belial-a8-feed',):
        result={}
        for action in ('test','build'):
            argv=['cargo',action,'--release','--offline','--locked','--jobs',str(jobs),'--manifest-path',str(ROOT/'engine_a51/Cargo.toml'),'--bin',name]
            with (out/(name+'_'+action+'.log')).open('w') as log:
                p=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                deadline=time.monotonic()+1800
                try:
                    while p.poll() is None:
                        ui.update(dict(phase='BUILDING',updated_ms=now_ms(),message=name+' '+action,orders=False))
                        if stop.value or time.monotonic()>deadline:raise RuntimeError('BUILD_CANCELLED_OR_TIMEOUT')
                        time.sleep(1)
                    result[action]=p.returncode
                    if p.returncode:raise RuntimeError('NATIVE_'+action.upper()+'_FAILED_'+name)
                finally:terminate(p)
        results[name]=dict(result,path=str(target/'release'/name),sha256=digest(target/'release'/name))
        atomic(out/'RESULTS.json',results,True)
    return target/'release/belial-a8-feed'

class Inbox:
    def __init__(self,stop):self.q=queue.Queue(maxsize=4096);self.stop=stop;self.overflows=0;self.lock=threading.Lock();self.samples=0;self.sample_reasons=collections.Counter()
    def put(self,row):
        if row['kind']=='sampling':
            with self.lock:
                self.samples+=1;self.sample_reasons[(row.get('chain'),row.get('pool'),row.get('reason','UNSPECIFIED'))]+=1
            return
        try:self.q.put_nowait(row)
        except queue.Full:
            with self.lock:self.overflows+=1
    def losses(self):
        with self.lock:return self.overflows,self.samples

def native_reader(proc,emit,stop):
    try:
        while not stop.is_set():
            line=proc.stdout.readline(4*1024*1024+1)
            if not line:break
            if len(line)>4*1024*1024 or not line.endswith(b'\n'):raise ValueError('NATIVE_FRAME_INVALID')
            emit(json.loads(line))
    except Exception as e:emit(dict(kind='gap',reason='NATIVE_READER_'+type(e).__name__))
    finally:
        if not stop.is_set():emit(dict(kind='fatal',reason='NATIVE_FEED_ENDED'))

def package(session,output):
    output.mkdir(parents=True,exist_ok=True)
    manifest={str(p.relative_to(session)):digest(p) for p in sorted(session.rglob('*')) if p.is_file() and p.name!='EVIDENCE_MANIFEST_SHA256.json'}
    atomic(session/'EVIDENCE_MANIFEST_SHA256.json',dict(files=manifest,orders=False),True)
    dest=output/('BELIAL_A8_2H_RESULTADO_'+session.name.removeprefix('m2-a8-')+'.zip')
    with zipfile.ZipFile(dest.with_suffix('.zip.partial'),'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
        for p in sorted(session.rglob('*')):
            if p.is_file():z.write(p,str(Path(session.name)/p.relative_to(session)))
    dest.with_suffix('.zip.partial').rename(dest);return dest

def run(output):
    if os.geteuid()==0:raise RuntimeError('EXECUTE_SEM_SUDO')
    if not Path('/mnt/sherlock-ssd').is_mount():raise RuntimeError('SSD_NOT_MOUNTED')
    runtime=BASE/'runtime/belial-a8';runtime.mkdir(parents=True,exist_ok=True)
    lock=(runtime/'run.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);stop=Stop().install();last=0
    while active_scans():
        if stop.value:return 130
        if time.monotonic()-last>30:print('A8_AGUARDANDO_SCAN_ANTERIOR; cronometro ainda nao iniciou',flush=True);last=time.monotonic()
        time.sleep(1)
    if shutil.disk_usage(BASE).free<8*1024**3:raise RuntimeError('SPACE_REQUIRED_8_GIB')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');session=BASE/'evidence'/('m2-a8-'+stamp);session.mkdir(parents=True)
    atomic(runtime/'ACTIVE_RUN.json',dict(session=str(session),release=str(ROOT),orders=False),True)
    ui=UI(session);print('A8_PAINEL='+ui.url,flush=True)
    # URL is deliberately not included in archived evidence; the listener is loopback only.
    if not os.environ.get('BELIAL_A1_NO_BROWSER') and shutil.which('xdg-open'):
        subprocess.Popen(['xdg-open',ui.url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    failures={};sol=None;bnb=None;entry_window_ended=False;final_readiness={};proc=None;reader=None;inputs=None;decisions=None;evidence=None;lab=None;sensors=None;endstop=threading.Event();started=None;ended=None;inbox=Inbox(endstop);doc={};last_state={};elapsed=0
    try:
        ui.update(dict(phase='PREPARING',updated_ms=now_ms(),message='Validando redes e liquidez; captura ainda nao iniciou',orders=False))
        audit=audit_writer(session/'RPC_DIAGNOSTICS.jsonl')
        sol=RPC(os.environ.get('BELIAL_SOLANA_RPC_URL','https://api.mainnet-beta.solana.com'),audit=audit,chain='solana')
        bnb=RPC(os.environ.get('BELIAL_BNB_RPC_URL',BNB_DEFAULT_RPC),audit=audit,chain='bsc')
        ws=os.environ.get('BELIAL_SOLANA_WS_URL','wss://api.mainnet-beta.solana.com')
        check_solana_mainnet(sol,session)
        print('A8_BNB_PROVIDER='+('CUSTOM_CONFIGURED' if os.environ.get('BELIAL_BNB_RPC_URL') else 'PUBLICNODE_BSC'),flush=True)
        check_bnb_capabilities(bnb,session)
        print('A8_BNB_LOGS_PREFLIGHT=PASS',flush=True)
        binary=native_build(session,runtime/'cargo-target',stop,ui)
        doc,metadata=prepare(session/'selection',sol,bnb,lambda symbol,n:ui.update(dict(phase='QUALIFYING',updated_ms=now_ms(),message='Validando '+symbol+'; '+str(n)+' contratos qualificados',orders=False)))
        if stop.value:raise RuntimeError('CANCELLED_DURING_PREPARATION')
        symbols=[c['symbol'] for c in doc['selected']];atomic(session/'selection.json',dict(symbols=symbols,orders=False))
        pools=[dict(p,symbol=c['symbol']) for c in doc['selected'] for p in c['sources']]
        decisions=Journal(session/'decisions.jsonl');inputs=Journal(session/'inputs.jsonl');evidence=gzip.open(session/'CHAIN_EVIDENCE.jsonl.gz','xt',encoding='utf-8')
        lab=Lab(doc['selected'],decisions.append)
        def record(row):
            at=now_ms()
            if at<lab.last_ms:raise RuntimeError('WALL_CLOCK_MOVED_BACKWARD')
            if row['kind']=='metadata':row=dict(row,data=dict(row['data'],symbols=[r for r in row['data'].get('symbols',[]) if r['symbol'] in symbols]))
            inputs.append(dict(at_ms=at,row=row));lab.ingest(row,at)
        record(dict(kind='metadata',received_ms=now_ms(),data=metadata))
        with (session/'native_stderr.log').open('wb') as err:
            proc=subprocess.Popen([str(binary),str(session/'selection.json')],cwd=ROOT,env=clean_env(),stdout=subprocess.PIPE,stderr=err,start_new_session=True)
        reader=threading.Thread(target=native_reader,args=(proc,inbox.put,endstop),daemon=True);reader.start()
        sensors=Sensors(pools,sol,bnb,ws,inbox.put,endstop);sensors.start()
        preparation_mono=time.monotonic();ready_deadline=preparation_mono+READINESS_TIMEOUT_SECONDS;startmono=None;lastview=0;lasttick=0;lastresources=0;last_readiness_log=0;losses=0;proofs=collections.OrderedDict()
        while not stop.value:
            mono=time.monotonic();at=now_ms()
            if started is not None:
                elapsed=int((mono-startmono)*1000)
                if elapsed>=DURATION_MS:
                    if not entry_window_ended:
                        record(dict(kind='end_entries'));entry_window_ended=True
                        print('A8_ENTRADAS_ENCERRADAS; acompanhamento de posicoes por ate 60 minutos',flush=True)
                    if not any(a.positions for a in lab.arms) or elapsed>=DURATION_MS+FOLLOWUP_MS:break
                if abs((at-started)-elapsed)>1000:raise RuntimeError('WALL_CLOCK_MONOTONIC_DIVERGENCE')
            elif mono>ready_deadline:
                status=capture_readiness(lab,now_ms(),int((mono-preparation_mono)*1000),PREPARATION_MS,CHAIN_STABLE_MS);atomic(session/'READINESS.json',status,True)
                raise RuntimeError('FEEDS_NOT_READY_WITHIN_360_SECONDS:'+','.join(status['blocked']))
            newloss,samples=inbox.losses()
            if newloss!=losses:record(dict(kind='gap',reason='SUPERVISOR_QUEUE_OVERFLOW',lost=newloss-losses));losses=newloss
            try:row=inbox.q.get(timeout=.05)
            except queue.Empty:row=None
            if started is not None and not entry_window_ended and now_ms()>=started+DURATION_MS:
                record(dict(kind='end_entries'));entry_window_ended=True
                print('A8_ENTRADAS_ENCERRADAS; acompanhamento de posicoes por ate 60 minutos',flush=True)
            if started is not None and (time.monotonic()-startmono)*1000>=DURATION_MS+FOLLOWUP_MS:
                elapsed=int((time.monotonic()-startmono)*1000);break
            if row:
                if row['kind']=='chain_evidence':
                    evidence.write(canonical(row)+'\n')
                    ident=row.get('signature') if row['chain']=='solana' else row['log']['transactionHash'].lower()
                    proofs[(row['chain'],ident,row['pool'])]=True
                    while len(proofs)>4096:proofs.popitem(last=False)
                elif row['kind']=='chain_trade' and (row['chain'],row['tx'],row['pool']) not in proofs:
                    record(dict(kind='chain_status',chain=row['chain'],state='SAMPLE_ERROR',reason='EVIDENCE_NOT_ARCHIVED'))
                elif row['kind']=='fatal':raise RuntimeError(row['reason'])
                else:record(row)
                if row['kind']=='chain_status' and row['state']=='FAILED':
                    failures['chain_'+row['chain']]=row.get('reason')
                    if started is None:raise RuntimeError('FEED_FAILED_'+row['chain']+':'+str(row.get('reason')))
                if row['kind']=='transport' and row.get('state')=='ACCESS_DENIED':raise RuntimeError('BINANCE_ACCESS_DENIED')
            if started is None:
                status=capture_readiness(lab,now_ms(),int((mono-preparation_mono)*1000),PREPARATION_MS,CHAIN_STABLE_MS)
                if mono-last_readiness_log>=10:
                    atomic(session/'READINESS.json',status)
                    print('A8_FEEDS='+str(status['fresh_books'])+'/20 livros; SOL='+status['chains']['solana']['state']+'; BNB='+status['chains']['bsc']['state']+'; pendencias='+','.join(status['blocked']),flush=True)
                    last_readiness_log=mono
                if status['ready']:
                    started=now_ms();startmono=time.monotonic();record(dict(kind='session',started_ms=started,deadline_ms=started+DURATION_MS))
                    atomic(session/'SESSION.json',dict(phase='CAPTURING',started_ms=started,duration_ms=DURATION_MS,orders=False),True)
                    print('A8_CAPTURA_2H_INICIADA='+str(started),flush=True)
            if mono-lasttick>=1:
                if started is not None:record(dict(kind='tick'))
                lasttick=mono
            if mono-lastview>=2:
                last_state=dict(phase=('FOLLOWUP' if entry_window_ended else 'CAPTURING') if started else 'CONNECTING',updated_ms=now_ms(),elapsed_ms=min(elapsed,DURATION_MS),lab=lab.view(now_ms()),readiness=(readiness(lab,now_ms()) if started else status),universe=doc['selected'],sampling=samples,failures=dict(failures),followup_ms=max(0,elapsed-DURATION_MS),orders=False)
                ui.update(last_state);lastview=mono
            if mono-lastresources>=5:
                from a6.resources import sample
                sample(session);lastresources=mono
                if shutil.disk_usage(BASE).free<1024**3:raise RuntimeError('DISK_RESERVE_1_GIB')
        if stop.value:failures['interrupted']='STOP_REQUESTED'
        ended=now_ms();record(dict(kind='finish'));final_readiness=readiness(lab,lab.last_ms);atomic(session/'FINAL_STATE.json',lab.view(lab.last_ms),True)
    except Exception as e:
        # Endpoint URLs/keys must never leak through arbitrary network error strings.
        failures['runtime']=error_label(e)
        print('A8_ERRO='+failures['runtime'],flush=True)
    finally:
        endstop.set();terminate(proc,5)
        if proc:
            if reader:reader.join(timeout=2)
            proc.stdout.close()
        if sensors:
            for t in sensors.threads:t.join(timeout=15)
        if lab and not (session/'FINAL_STATE.json').exists():
            ended=max(now_ms(),lab.last_ms);row=dict(kind='finish');inputs.append(dict(at_ms=ended,row=row));lab.ingest(row,ended);atomic(session/'FINAL_STATE.json',lab.view(lab.last_ms),True)
        for f in (inputs,decisions,evidence):
            if f:f.close()
        if lab:
            try:
                from a8.replay import replay
                replay(session)
            except Exception as e:failures['replay']=type(e).__name__+':'+str(e)
            try:
                from a8.funding import reconcile
                reconcile(session,ended)
            except Exception as e:failures['funding']=type(e).__name__
        final_state=read_json(session/'FINAL_STATE.json',{}) or {}
        if final_state:
            from a8.report import compare
            try:compare(session,final_state)
            except Exception as e:failures['comparison']=type(e).__name__
        complete=started is not None and elapsed>=DURATION_MS and 'runtime' not in failures and 'interrupted' not in failures
        final_readiness=readiness(lab,lab.last_ms) if lab else {}
        if lab:atomic(session/'READINESS.json',final_readiness,True)
        summary=dict(readiness=final_readiness,phase=('FINISHED_DEGRADED' if failures else 'FINISHED') if complete else 'FAILED_OR_INTERRUPTED',updated_ms=now_ms(),elapsed_ms=min(elapsed,DURATION_MS),
            duration_requested_ms=DURATION_MS,followup_ms=max(0,elapsed-DURATION_MS),followup_limit_ms=FOLLOWUP_MS,capture_complete=complete,coverage_complete=complete and not failures and len(lab.quality)==20 and all(v['ticks']>0 and v['ALL_DATA_READY']==v['ticks'] for v in lab.quality.values()),observation_end_ms=lab.last_ms if lab else ended,failures=failures,lab=final_state,universe=doc.get('selected',[]),sampling=inbox.losses()[1],orders=False,
            message='Ensaio exploratorio; duas horas nao estabelecem rentabilidade.',gpu='NOT_USED_NO_VALIDATED_MODEL_FOR_THIS_TARGET')
        atomic(session/'SAMPLING.json',dict(total=inbox.samples,by_pool=[dict(chain=k[0],pool=k[1],reason=k[2],count=v) for k,v in sorted(inbox.sample_reasons.items())],orders=False),True)
        atomic(session/'A8_SUMMARY.json',summary,True);atomic(session/'SESSION.json',dict(phase=summary['phase'],started_ms=started,ended_ms=ended,orders=False),True)
        final_html(session,summary);ui.update(summary);ui.close()
        result=package(session,output);atomic(runtime/'LAST_RESULT.json',dict(zip=str(result),session=str(session),orders=False),True)
        print('BELIAL_A8_RESULTADO='+str(result),flush=True)
        report=(session/'RELATORIO.html').resolve()
        print('A8_RELATORIO_LOCAL='+str(report),flush=True)
        if failures and not os.environ.get('BELIAL_A1_NO_BROWSER') and shutil.which('xdg-open'):
            subprocess.Popen(['xdg-open',str(report)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    lock.close()
    return 0 if complete and not failures else 2
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=Path('/home/hobit/Downloads'));args=p.parse_args();raise SystemExit(run(args.output))
