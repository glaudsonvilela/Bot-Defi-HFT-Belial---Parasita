"""Real native gates, one binary at a time. Never represents lexical checks as compilation."""
from __future__ import annotations
import argparse,shutil,subprocess
from a51.common import *
BINS=(('perp','belial-perp-feed'),('raw','belial-spot-raw'),('spot','belial-r5c2-live-profiler'))
def run(session,target):
    out=Path(session)/'build';out.mkdir(exist_ok=True);results={};stop=Stop().install();cargo=shutil.which('cargo')
    for kind,name in BINS:
        row={'binary':name,'ok':False,'native_tests_executed':False,'native_build_executed':False}
        if not cargo:row['error']='CARGO_UNAVAILABLE_NO_AUTOMATIC_INSTALL'
        elif stop.value:row['error']='BUILD_CANCELLED'
        else:
            env=clean_env();env.update(CARGO_TARGET_DIR=str(target),CARGO_NET_OFFLINE='true',TOKIO_WORKER_THREADS='1')
            for action in ('test','build'):
                argv=[cargo,action,'--release','--locked','--offline','--jobs','1','--manifest-path',str(ROOT/'engine_a51/Cargo.toml'),'--bin',name]
                heartbeat(out/'status.json','BUILDING',component=kind,command=action)
                log=out/(kind+'_'+action+'.log')
                with log.open('w') as f:
                    f.write(canonical({'command':argv})+'\n');f.flush()
                    p=subprocess.Popen(argv,stdout=f,stderr=subprocess.STDOUT,cwd=ROOT,env=env,start_new_session=True)
                    end=time.monotonic()+1800
                    started=time.monotonic();last=0
                    while p.poll() is None and not stop.value and time.monotonic()<end:
                        if time.monotonic()-last>=1:
                            with log.open('rb') as reader:
                                reader.seek(max(0,log.stat().st_size-3000));tail=reader.read().decode(errors='replace')[-1800:]
                            heartbeat(out/'status.json','BUILDING',component=kind,command=action,elapsed_seconds=round(time.monotonic()-started,1),last_log=tail,native_pid=p.pid,progress_percent=None)
                            last=time.monotonic()
                        time.sleep(.2)
                    if p.poll() is None:terminate(p,5);row['error']='BUILD_TIMEOUT_OR_CANCELLED'
                row['native_tests_executed' if action=='test' else 'native_build_executed']=True
                row[action+'_returncode']=p.returncode
                if p.returncode!=0:row['error']=row.get('error',action.upper()+'_FAILED');break
            binary=Path(target)/'release'/name
            if row.get('test_returncode')==row.get('build_returncode')==0 and binary.is_file():
                row.update(ok=True,path=str(binary),sha256=digest(binary))
        results[kind]=row;atomic(out/'RESULTS.json',results,durable=True)
    heartbeat(out/'status.json','COMPLETE',results=results);atomic(out/'DONE.json',{'ok':all(r['ok'] for r in results.values()),'results':results})
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--session',type=Path,required=True);ap.add_argument('--target',type=Path,required=True);a=ap.parse_args();raise SystemExit(run(a.session,a.target))
