#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
import common as c
from d1_patch import patch as patch_d1

N1_CONFIG_SHA='e92e0b56b33b4157a82524f280e3e7626fd6f6be4ba571645576fb26100705b9'
M2_CONFIG_SHA='d7bf243539e17176b682b66ef40d907668df6ecbcaf26258e608e393531fca2a'
M2_ENGINE_SHA='47374b3df15952cde9b2f16a6cd069324312122954f54ae7ba0c7d0cc4128988'
RULES_SHA='e2f99e9ef7918fb20c53c3aea40bcde385e4ea2527f063879cc600078ac464d3'

def run(cmd,log,env=None,timeout=180):
    cp=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=timeout);combined=(cp.stdout or '')+(cp.stderr or '');log.write_text(combined)
    if cp.returncode:
        if combined:
            print('--- COMMAND FAILURE LOG: '+str(log)+' ---',file=sys.stderr,flush=True)
            print(combined[-12000:],file=sys.stderr,flush=True)
            print('--- END COMMAND FAILURE LOG ---',file=sys.stderr,flush=True)
        raise RuntimeError('COMMAND_FAILED:'+str(log))
    return cp

def verify_payload(payload):
    man=json.loads((payload/'PAYLOAD_SHA256.json').read_text())
    for rel,sha in man.items():
        p=payload/rel
        if not p.is_file() or c.digest(p)!=sha:raise RuntimeError('PAYLOAD_HASH_MISMATCH:'+rel)

def preflight(base):
    n1=base/'releases/r5d-n1-numeric-v1';m2=base/'releases/r5d-m2-live-v1';rules=base/'runtime/r5d/FROZEN_R5D_TAIL_RULES.json'
    for p in (n1,m2,rules):
        c.safe(p)
        if not p.exists():raise RuntimeError('REQUIRED_COMPONENT_MISSING:'+str(p))
    checks=[(n1/'CONFIG_N1.json',N1_CONFIG_SHA),(m2/'CONFIG_M2.json',M2_CONFIG_SHA),(m2/'bin/belial-r5c2-live-profiler',M2_ENGINE_SHA),(rules,RULES_SHA)]
    for p,sha in checks:
        if not p.is_file() or c.digest(p)!=sha:raise RuntimeError('REQUIRED_COMPONENT_VERSION_MISMATCH:'+str(p))
    icd=Path('/etc/OpenCL/vendors/nvidia.icd')
    if not icd.is_file() or 'nvidia' not in icd.read_text(errors='ignore').lower():raise RuntimeError('NVIDIA_OPENCL_ICD_NOT_FOUND')
    if not Path('/lib/x86_64-linux-gnu/libOpenCL.so.1').exists() and not Path('/usr/lib/x86_64-linux-gnu/libOpenCL.so.1').exists():raise RuntimeError('OPENCL_LOADER_NOT_FOUND')
    if shutil.which('nvidia-smi') is None:raise RuntimeError('NVIDIA_SMI_NOT_FOUND')
    return {'n1':str(n1),'m2':str(m2),'rules':str(rules),'m2_engine_sha256':M2_ENGINE_SHA}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--payload',type=Path,required=True);ap.add_argument('--base',type=Path,default=c.BASE);a=ap.parse_args();payload=a.payload.resolve();base=a.base.resolve();c.safe(payload);c.safe(base);print('[1/4] Verificando payload, N1, M2 e OpenCL...',flush=True);verify_payload(payload);pf=preflight(base)
    release=base/'releases'/c.RELEASE;runtime=base/'runtime'/c.RUNTIME;c.safe(release);c.safe(runtime)
    if release.exists():raise RuntimeError('N2_ALREADY_INSTALLED_USE_RUN_N2_WATCH_SH')
    stamp=c.stamp();report=runtime/'reports'/('n2-install-'+stamp);report.mkdir(parents=True,exist_ok=False);env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    with tempfile.TemporaryDirectory(prefix='.n2-stage-',dir=str(base/'releases')) as td:
        stage=Path(td)/'release';shutil.copytree(payload,stage);env['PYTHONPATH']=str(stage/'tools')
        print('[2/4] Testes offline da N2...',flush=True);run([sys.executable,'-B','-m','unittest','discover','-s',str(stage/'tests'),'-v'],report/'tests.log',env,120)
        print('[3/4] Autoteste e benchmark OpenCL na GPU...',flush=True);gpu=run([sys.executable,'-B',str(stage/'tools/validate_gpu.py'),'--out',str(report/'gpu_validation.json')],report/'gpu_validation.log',env,240)
        gv=json.loads((report/'gpu_validation.json').read_text())
        if not gv.get('BELIAL_N2_GPU_VALIDATION_PASS'):raise RuntimeError('GPU_VALIDATION_NOT_PASS')
        os.rename(stage,release)
    print('[4/4] Instalando watcher shadow e integração opcional com D1...',flush=True)
    backup=base/'.belial-backups'/('r5d-n2-'+stamp);backup.mkdir(parents=True,exist_ok=False);rollback=backup/'ROLLBACK_N2.sh'
    d1=patch_d1(base,release,backup/'d1')
    rb='#!/usr/bin/env bash\nset -euo pipefail\nBASE='+repr(str(base))+'\npython3 -B "$BASE/releases/'+c.RELEASE+'/tools/control.py" --stop --base "$BASE" || true\n'
    rb+='if [ -f '+repr(str(backup/'d1/d1_dashboard.py.before'))+' ] && [ -d "$BASE/releases/r5d-d1-dashboard-v1" ]; then\n  python3 -B "$BASE/releases/r5d-d1-dashboard-v1/tools/control.py" --stop || true\n  cp '+repr(str(backup/'d1/d1_dashboard.py.before'))+' "$BASE/releases/r5d-d1-dashboard-v1/tools/dashboard.py"\n  python3 -B "$BASE/releases/r5d-d1-dashboard-v1/tools/control.py" --start --max-hz 120 || true\nfi\n'
    rb+='TS=$(date -u +%Y%m%dT%H%M%SZ)\nmkdir -p '+repr(str(backup))+'/rollback-$TS\n[ ! -d "$BASE/releases/'+c.RELEASE+'" ] || mv "$BASE/releases/'+c.RELEASE+'" '+repr(str(backup))+'/rollback-$TS/release\n[ ! -d "$BASE/runtime/'+c.RUNTIME+'" ] || mv "$BASE/runtime/'+c.RUNTIME+'" '+repr(str(backup))+'/rollback-$TS/runtime-preserved\necho BELIAL_N2_ROLLBACK_DONE=true\n'
    rollback.write_text(rb);rollback.chmod(0o700)
    start=run([sys.executable,'-B',str(release/'tools/control.py'),'--start','--base',str(base)],report/'start.log',env,60)
    status=c.read_json(runtime/'status.json',{}) or {};result={'BELIAL_N2_OK':True,'RELEASE':str(release),'RUNTIME':str(runtime),'REPORT':str(report),'ROLLBACK':str(rollback),'WATCHER':start.stdout.strip(),'GPU_DEVICE':gv.get('device'),'SELECTED_DIMENSION':gv.get('selected_dimension'),'SELECTED_HEADS':gv.get('selected_heads'),'GPU_BENCHMARK_STAGES':gv.get('stages'),'N1_CONTROL_PRESERVED':True,'M2_COLLECTOR_MODIFIED':False,'D1_MODIFIED':bool(d1.get('applied')),'D1_INTEGRATION':d1,'FROZEN_RULES_MODIFIED':False,'LIVE_MODEL_SEEDED_FROM_REPLAY':False,'LIVE_PROMOTED':False,'SIGNAL_ENGINE_FEEDBACK':False,'TRANSACTIONS_SUBMITTED':False,'STATUS_AT_INSTALL':status}
    c.atomic_json(report/'RESULT.json',result);print(json.dumps(result,indent=2,sort_keys=True));print('BELIAL_N2_OK=true');return 0
if __name__=='__main__':raise SystemExit(main())
