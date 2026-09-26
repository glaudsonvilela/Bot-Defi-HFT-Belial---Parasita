"""Install into a separate content-checked release. Never modify previous versions."""
from __future__ import annotations
import fcntl,os,shutil,sys,tempfile
from pathlib import Path
from a3.common import ROOT,BASE,safe,clean_env,digest
from a3.verify import verify_files,test_suites
RELEASE='belial-a3-90m-v1'

def install(root=ROOT,base=BASE):
    if os.geteuid()==0:raise RuntimeError('Execute como usuário normal, sem sudo/root.')
    safe(base)
    if not Path('/mnt/sherlock-ssd').is_mount():raise RuntimeError('SSD não montado; nenhuma instalação ou captura iniciada.')
    if not base.is_dir():raise RuntimeError('Base Belial não encontrada: '+str(base))
    if shutil.disk_usage(base).free<3*1024**3:raise RuntimeError('Menos de 3 GiB livres; preserve espaço para compilação e evidências.')
    releases=safe(base/'releases');releases.mkdir(exist_ok=True)
    destination=safe(releases/RELEASE)
    print('A3 1/3 | Integridade e testes offline; nenhum serviço anterior será encerrado.',flush=True)
    verify_files(root);test_suites(root)
    lock=(releases/'.belial-a3-install.lock').open('a+')
    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        if destination.exists():
            verify_files(destination)
            if digest(destination/'MANIFEST_SHA256.json')!=digest(root/'MANIFEST_SHA256.json'):
                raise RuntimeError('Release A3 local difere do pacote; nenhuma alteração foi aplicada.')
            print('A3_RELEASE_REUSED_EXACT_HASHES=true',flush=True)
        else:
            staging=Path(tempfile.mkdtemp(prefix='.belial-a3-staging-',dir=releases))
            try:
                shutil.copytree(root,staging,dirs_exist_ok=True)
                verify_files(staging)
                staging.rename(destination)
            finally:
                if staging.exists():shutil.rmtree(staging)
            print('A3_RELEASE_INSTALLED='+str(destination),flush=True)
    finally:
        lock.close()
    print('A3 2/3 | Painel e supervisor; testes/build nativos por componente; fontes em preparação.',flush=True)
    print('A3 3/3 | A captura será de 90 minutos, após a preparação; cobertura será explícita.',flush=True)
    env=clean_env();env['PYTHONPATH']=str(destination)
    os.chdir(destination)
    os.execve(sys.executable,[sys.executable,'-B','-m','a3.supervisor'],env)

if __name__=='__main__':
    try:install()
    except Exception as e:print('BELIAL_A3_INSTALL_FAILED='+str(e),file=sys.stderr);raise SystemExit(2)
