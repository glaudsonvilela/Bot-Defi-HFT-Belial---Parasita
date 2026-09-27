"""Offline verification for isolated A5.1 16Q 9-hour package."""
from __future__ import annotations
import argparse,ast,json,os,re,subprocess,sys
from pathlib import Path
from a51.common import ROOT,digest,safe
MANIFEST='MANIFEST_SHA256.json'

def verify_files(root=ROOT):
    root=safe(root);manifest=json.loads((root/MANIFEST).read_text());files=manifest.get('files')
    if manifest.get('schema')!='belial_a51_16q_9h_package_v1' or not isinstance(files,dict) or not files:raise RuntimeError('INVALID_A51_PACKAGE_MANIFEST')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and str(p.relative_to(root))!=MANIFEST}
    for p in root.rglob('*'):safe(p)
    if actual!=set(files):raise RuntimeError('A51_PACKAGE_MEMBERS_CHANGED:'+str(sorted(actual.symmetric_difference(files))[:30]))
    for rel,expected in files.items():
        p=Path(rel)
        if p.is_absolute() or '..' in p.parts or not re.fullmatch('[0-9a-f]{64}',str(expected)):raise RuntimeError('INVALID_A51_MANIFEST_PATH_OR_DIGEST')
        if digest(root/p)!=expected:raise RuntimeError('A51_PACKAGE_HASH_MISMATCH:'+rel)
    return len(files)

def test_suite(root=ROOT,output=None):
    root=Path(root);env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root),BELIAL_A1_NO_BROWSER='1');argv=[sys.executable,'-B','-m','unittest','discover','-s',str(root/'tests_a51'),'-v'];p=subprocess.run(argv,cwd=root,env=env,capture_output=True,text=True,timeout=300);log=p.stdout+p.stderr;match=re.search(r'Ran (\d+) tests?',log);n=int(match.group(1)) if match else 0
    if output:output=Path(output);output.mkdir(parents=True,exist_ok=True);(output/'a51_tests.log').write_text(log)
    passed=p.returncode==0 and n>0;print('A51_TESTS='+str(n)+'; PASS='+str(passed).lower(),flush=True)
    if not passed:print(log[-30000:],file=sys.stderr);raise RuntimeError('A51_OFFLINE_SUITE_FAILED')
    return n

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--hashes-only',action='store_true');ap.add_argument('--log-dir',type=Path);a=ap.parse_args()
    if a.log_dir:
        resolved=a.log_dir.resolve()
        if resolved==ROOT or ROOT in resolved.parents:raise SystemExit('Logs must stay outside the sealed release.')
    n=verify_files();print('A51_PACKAGE_HASHES=PASS; FILES='+str(n),flush=True)
    if not a.hashes_only:
        for f in ROOT.rglob('*.py'):ast.parse(f.read_text(),filename=str(f))
        tests=test_suite(output=a.log_dir);result={'ok':True,'tests':tests,'orders':False,'external_market_test':False,'native_rust_executed':False}
        if a.log_dir:(a.log_dir/'VERIFY_RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
        print('A51_OFFLINE_VERIFIED='+str(tests)+'; RUST=NOT_EXECUTED; ORDERS=false',flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
