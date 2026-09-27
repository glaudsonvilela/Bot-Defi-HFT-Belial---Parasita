#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
import common as c

def alive(pid):
    try:os.kill(pid,0);return True
    except (OSError,ProcessLookupError):return False

def main():
    ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True);g.add_argument('--start',action='store_true');g.add_argument('--stop',action='store_true');g.add_argument('--status',action='store_true');ap.add_argument('--base',type=Path,default=c.BASE);a=ap.parse_args();base=a.base.resolve();rt=base/'runtime'/c.RUNTIME;rel=base/'releases'/c.RELEASE;rt.mkdir(parents=True,exist_ok=True);pidp=rt/'watch.pid';stp=rt/'STOP_N2';log=rt/'watch.log'
    if a.status:
        out=c.read_json(rt/'status.json',{'state':'NOT_STARTED'});out['pid']=int(pidp.read_text()) if pidp.is_file() else None;out['pid_alive']=alive(out['pid']) if out['pid'] else False;print(json.dumps(out,indent=2,sort_keys=True));return 0
    if a.stop:
        stp.write_text('stop\n')
        if pidp.is_file():
            try:
                pid=int(pidp.read_text());os.kill(pid,signal.SIGTERM)
                for _ in range(40):
                    if not alive(pid):break
                    time.sleep(.1)
            except Exception:pass
        print('BELIAL_N2_STOP_REQUESTED=true');return 0
    if pidp.is_file():
        try:
            pid=int(pidp.read_text())
            if alive(pid):print('BELIAL_N2_ALREADY_RUNNING=true');return 0
        except Exception:pass
    stp.unlink(missing_ok=True)
    with log.open('ab',buffering=0) as f:
        p=subprocess.Popen([sys.executable,'-B',str(rel/'tools/live.py'),'--base',str(base),'--runtime',str(rt)],stdout=f,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
    tmp=pidp.with_name(pidp.name+'.tmp-'+str(os.getpid()));tmp.write_text(str(p.pid)+'\n');os.replace(tmp,pidp)
    for _ in range(100):
        time.sleep(.1);st=c.read_json(rt/'status.json',{}) or {}
        if st.get('state') not in (None,'GPU_INITIALIZING'):break
        if p.poll() is not None:break
    if p.poll() is not None:raise RuntimeError('N2_WATCHER_EXITED_EARLY_SEE_'+str(log))
    print(json.dumps({'BELIAL_N2_STARTED':True,'PID':p.pid,'STATUS':str(rt/'status.json'),'LOG':str(log)},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
