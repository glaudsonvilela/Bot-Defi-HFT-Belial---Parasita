"""Sealed-package validation and functional tests; no external network."""
import ast,hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def verify(root=ROOT):
    root=Path(root);doc=json.loads((root/'A8_MANIFEST_SHA256.json').read_text());expected=doc['files']
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name!='A8_MANIFEST_SHA256.json'}
    if set(expected)!=actual:raise RuntimeError('A8_MEMBERS_MISMATCH:'+str(sorted(set(expected)^actual)[:10]))
    for name,digest in expected.items():
        p=root/name
        if p.is_symlink() or any(q.is_symlink() for q in p.parents) or Path(name).is_absolute() or '..' in Path(name).parts:raise RuntimeError('UNSAFE_PACKAGE_PATH')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('HASH_MISMATCH:'+name)
    for p in root.rglob('*.py'):ast.parse(p.read_text(),filename=str(p))
    print('A8_HASHES_AND_PYTHON_SYNTAX=PASS; FILES='+str(len(expected)),flush=True)
    return doc

def tests(root=ROOT):
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root));env['BELIAL_A1_NO_BROWSER']='1'
    for folder in ('tests_a8','tests_a7','tests_a6'):
        subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(root/folder),'-v'],cwd=root,env=env,check=True,timeout=180)
if __name__=='__main__':
    verify()
    if '--hashes-only' not in sys.argv:tests()
