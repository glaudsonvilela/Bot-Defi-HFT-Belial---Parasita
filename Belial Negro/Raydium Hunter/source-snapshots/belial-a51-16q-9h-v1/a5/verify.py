"""Offline A5 package verification."""
from __future__ import annotations
import argparse,ast,json,os,re,subprocess,sys
from pathlib import Path
from a5.common import ROOT,digest,safe
MANIFEST='MANIFEST_SHA256.json'
GROUPS=('mp12','n2_mp12','d2','runner','perp','a1','a2','n2_a2','labels_a2','a3','a4','a5')

def verify_files(root=ROOT):
    root=safe(root)
    manifest=json.loads((root/MANIFEST).read_text())
    files=manifest.get('files')
    if manifest.get('schema')!='belial_a5_16q_package_v1' or not isinstance(files,dict) or not files:
        raise RuntimeError('INVALID_A5_PACKAGE_MANIFEST')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and str(p.relative_to(root))!=MANIFEST}
    for p in root.rglob('*'):safe(p)
    if actual!=set(files):raise RuntimeError('A5_PACKAGE_MEMBERS_CHANGED:'+str(sorted(actual.symmetric_difference(files))[:20]))
    for relative,expected in files.items():
        p=Path(relative)
        if p.is_absolute() or '..' in p.parts or not re.fullmatch('[0-9a-f]{64}',str(expected)):
            raise RuntimeError('INVALID_A5_MANIFEST_PATH_OR_DIGEST')
        if digest(root/p)!=expected:raise RuntimeError('A5_PACKAGE_HASH_MISMATCH:'+relative)
    return len(files)

def test_suites(root=ROOT,output=None):
    root=Path(root);results={}
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root),BELIAL_A1_NO_BROWSER='1')
    for group in GROUPS:
        if group in ('a1','a2','labels_a2','a3','a4','a5'):cwd=root
        elif group=='n2_a2':cwd=root/'n2_a2'
        else:cwd=root/'legacy'/group
        if group=='a5':directory=root/'tests_a5'
        elif group=='a4':directory=root/'tests_a4'
        elif group=='a3':directory=root/'tests_a3'
        elif group in ('a1','a2'):directory=root/'tests'
        elif group=='labels_a2':directory=root/'tests_n2_a2'
        else:directory=cwd/'tests'
        argv=[sys.executable,'-B','-m','unittest','discover','-s',str(directory),'-v']
        if group in ('a1','a2'):argv+=['-p','test_'+group+'_*.py']
        p=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,timeout=240)
        log=p.stdout+p.stderr;match=re.search(r'Ran (\d+) tests?',log)
        n=int(match.group(1)) if match else 0
        results[group]={'tests':n,'returncode':p.returncode,'passed':p.returncode==0 and n>0,'skips':len(re.findall(r'\.\.\. skipped',log))}
        if output:
            output=Path(output);output.mkdir(parents=True,exist_ok=True);(output/(group+'_tests.log')).write_text(log)
        print('A5_TESTS='+group+'; TESTS='+str(n)+'; PASS='+str(results[group]['passed']).lower(),flush=True)
        if not results[group]['passed'] or results[group]['skips']:
            print(log[-30000:],file=sys.stderr);raise RuntimeError('A5_OFFLINE_SUITE_FAILED:'+group)
    return results

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--hashes-only',action='store_true');ap.add_argument('--log-dir',type=Path);a=ap.parse_args()
    if a.log_dir and (a.log_dir.resolve()==ROOT or ROOT in a.log_dir.resolve().parents):raise SystemExit('Logs devem ficar fora da release selada.')
    n=verify_files();print('A5_PACKAGE_HASHES=PASS; FILES='+str(n),flush=True)
    if not a.hashes_only:
        for f in ROOT.rglob('*.py'):ast.parse(f.read_text(),filename=str(f))
        results=test_suites(output=a.log_dir)
        summary={'ok':True,'tests':sum(v['tests'] for v in results.values()),'groups':results,'native_rust_executed':False,'external_market_test':False,'gpu_executed':False,'orders':False}
        if a.log_dir:(a.log_dir/'VERIFY_RESULT.json').write_text(json.dumps(summary,indent=2))
        print('A5_OFFLINE_VERIFIED='+str(summary['tests'])+'; RUST=NOT_EXECUTED; ORDERS=false',flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
