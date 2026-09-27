#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
import common as c
OLD_SHA='389c235e96e107e2dc54af57dc368b94e7aa200d518e3a3efc55165879cafac4'
NEW_SHA='9b2ac0b119d325e44dbb720a9e010f56999acc8cf0ada84d1d6e2474b089f381'

def patch(base:Path,payload:Path,backup:Path):
    rel=base/'releases/r5d-d1-dashboard-v1';dash=rel/'tools/dashboard.py';ctrl=rel/'tools/control.py';rt=base/'runtime/r5d-d1'
    if not rel.is_dir():return {'applied':False,'reason':'D1_NOT_INSTALLED'}
    if not dash.is_file() or not ctrl.is_file():return {'applied':False,'reason':'D1_LAYOUT_UNKNOWN','review_required':True}
    sha=c.digest(dash)
    if sha==NEW_SHA:return {'applied':False,'reason':'D1_N2_PATCH_ALREADY_PRESENT','sha256':sha}
    if sha!=OLD_SHA:return {'applied':False,'reason':'D1_SOURCE_CHANGED_NOT_PATCHED','sha256':sha,'review_required':True}
    status=subprocess.run([sys.executable,'-B',str(ctrl),'--status'],capture_output=True,text=True,timeout=10)
    running=False
    if status.returncode==0:
        try:running=bool(json.loads(status.stdout).get('running'))
        except Exception:pass
    if running:subprocess.run([sys.executable,'-B',str(ctrl),'--stop'],capture_output=True,text=True,timeout=15,check=True)
    backup.mkdir(parents=True,exist_ok=True);shutil.copy2(dash,backup/'d1_dashboard.py.before')
    src=payload/'extras/d1_dashboard_n2.py'
    if c.digest(src)!=NEW_SHA:raise RuntimeError('D1_PATCH_PAYLOAD_HASH')
    tmp=dash.with_name('dashboard.py.n2tmp-'+str(os.getpid()));shutil.copy2(src,tmp);os.chmod(tmp,0o755)
    cp=subprocess.run([sys.executable,'-m','py_compile',str(tmp)],capture_output=True,text=True,timeout=10)
    if cp.returncode:
        tmp.unlink(missing_ok=True);raise RuntimeError('D1_PATCH_COMPILE_FAILED:'+cp.stderr)
    os.replace(tmp,dash)
    if c.digest(dash)!=NEW_SHA:raise RuntimeError('D1_PATCH_FINAL_HASH')
    restarted=None
    if running:
        r=subprocess.run([sys.executable,'-B',str(ctrl),'--start','--max-hz','120'],capture_output=True,text=True,timeout=20)
        restarted={'returncode':r.returncode,'stdout':r.stdout.strip(),'stderr':r.stderr.strip()}
        if r.returncode:raise RuntimeError('D1_RESTART_FAILED_AFTER_PATCH')
    return {'applied':True,'old_sha256':OLD_SHA,'new_sha256':NEW_SHA,'was_running':running,'restart':restarted,'pnl_logic_modified':False,'collector_modified':False}
