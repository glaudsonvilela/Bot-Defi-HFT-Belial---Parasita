"""Offline package verification. Test outputs never alter the sealed release."""
from __future__ import annotations
import argparse,ast,hashlib,json,os,re,subprocess,sys,tempfile
from pathlib import Path
from a2.common import ROOT,canonical,digest,safe
MANIFEST='MANIFEST_SHA256.json'
GROUPS=('mp12','n2_mp12','d2','runner','perp','a1','a2','n2_a2','labels_a2')

def verify_files(root=ROOT):
    root=safe(root)
    manifest=json.loads((root/MANIFEST).read_text())
    files=manifest.get('files')
    if manifest.get('schema')!='belial_a2_package_v1' or not isinstance(files,dict) or not files:
        raise RuntimeError('INVALID_PACKAGE_MANIFEST')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and str(p.relative_to(root))!=MANIFEST}
    for p in root.rglob('*'):safe(p)
    if actual!=set(files):raise RuntimeError('PACKAGE_MEMBERS_CHANGED:'+str(sorted(actual.symmetric_difference(files))[:12]))
    for relative,expected in files.items():
        p=Path(relative)
        if p.is_absolute() or '..' in p.parts or not re.fullmatch('[0-9a-f]{64}',str(expected)):
            raise RuntimeError('INVALID_MANIFEST_PATH_OR_DIGEST')
        if digest(root/p)!=expected:raise RuntimeError('PACKAGE_HASH_MISMATCH:'+relative)
    return len(files)

def test_suites(root=ROOT,output=None):
    root=Path(root);results={}
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root),BELIAL_A1_NO_BROWSER='1')
    for group in GROUPS:
        cwd=root if group in ('a1','a2','labels_a2') else root/'n2_a2' if group=='n2_a2' else root/'legacy'/group
        directory=root/'tests' if group in ('a1','a2') else root/'tests_n2_a2' if group=='labels_a2' else cwd/'tests'
        argv=[sys.executable,'-B','-m','unittest','discover','-s',str(directory),'-v']
        if group in ('a1','a2'):argv+=['-p','test_'+group+'_*.py']
        p=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,timeout=180)
        log=p.stdout+p.stderr;match=re.search(r'Ran (\d+) tests?',log)
        n=int(match.group(1)) if match else 0
        results[group]={'tests':n,'returncode':p.returncode,'passed':p.returncode==0 and n>0,'skips':len(re.findall(r'\.\.\. skipped',log))}
        if output:
            output=Path(output);output.mkdir(parents=True,exist_ok=True);(output/(group+'_tests.log')).write_text(log)
        print('A2_TESTS='+group+'; TESTS='+str(n)+'; PASS='+str(results[group]['passed']).lower(),flush=True)
        if not results[group]['passed'] or results[group]['skips']:
            print(log[-24000:],file=sys.stderr);raise RuntimeError('OFFLINE_SUITE_FAILED:'+group)
    return results

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--hashes-only',action='store_true');ap.add_argument('--log-dir',type=Path);a=ap.parse_args()
    if a.log_dir and (a.log_dir.resolve()==ROOT or ROOT in a.log_dir.resolve().parents):raise SystemExit('Logs devem ficar fora da release selada.')
    n=verify_files();print('A2_PACKAGE_HASHES=PASS; FILES='+str(n),flush=True)
    if not a.hashes_only:
        for f in ROOT.rglob('*.py'):ast.parse(f.read_text(),filename=str(f))
        results=test_suites(output=a.log_dir)
        summary={'ok':True,'tests':sum(v['tests'] for v in results.values()),'groups':results,'native_rust_executed':False,'external_market_test':False,'gpu_executed':False}
        if a.log_dir:(a.log_dir/'VERIFY_RESULT.json').write_text(json.dumps(summary,indent=2))
        print('A2_OFFLINE_VERIFIED='+str(summary['tests'])+'; RUST=NOT_EXECUTED; ORDERS=false',flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
