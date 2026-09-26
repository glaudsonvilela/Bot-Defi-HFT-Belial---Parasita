#!/usr/bin/env python3
"""Self-contained, append-only T3 installation. Never writes to T2/N1 releases.
Dependencies remain the inherited offline Cargo lock + Python standard library.
"""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile,time,zipfile
from pathlib import Path
from rpc_transport import archive_file, public_endpoint, redact_text
from session_config import CAPTURE_MINUTES
BASE=Path('/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter')
DESKTOP=Path.home()/'Área de trabalho/Altside'

def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 return h.hexdigest()

def tree_manifest(root,skip=()):
 files={}
 for p in sorted(Path(root).rglob('*')):
  if p.is_symlink():raise RuntimeError('SYMLINK_REFUSED:'+str(p))
  rel=str(p.relative_to(root))
  if p.is_file() and rel not in skip and '__pycache__' not in p.parts and p.suffix!='.pyc':files[rel]=digest(p)
 return files

def run(cmd,log,env=None,timeout=1800,stream=False):
 log=Path(log);log.parent.mkdir(parents=True,exist_ok=True)
 endpoints=[(env or {}).get('BELIAL_SOLANA_RPC_URL',''),(env or {}).get('BELIAL_SOLANA_WS_URL','')]
 if not stream:
  with log.open('w') as f:
   p=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env=env,timeout=timeout)
  code=p.returncode
 else:
  with log.open('w') as f, log.open('r') as reader:
   process=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,env=env)
   deadline=time.monotonic()+timeout
   try:
    while process.poll() is None:
     chunk=reader.read()
     if chunk:print(redact_text(chunk,endpoints),end='',flush=True)
     if time.monotonic()>=deadline:
      process.terminate()
      try:process.wait(timeout=5)
      except subprocess.TimeoutExpired:process.kill();process.wait()
      raise RuntimeError('COMMAND_TIMEOUT:'+str(log))
     time.sleep(.1)
    chunk=reader.read()
    if chunk:print(redact_text(chunk,endpoints),end='',flush=True)
    code=process.returncode
   finally:
    if process.poll() is None:
     process.kill();process.wait()
 if code:
  if not stream:print(redact_text(log.read_text(errors='replace')[-24000:],endpoints),file=sys.stderr)
  raise RuntimeError('COMMAND_FAILED:'+str(log))

def install_dir(src,dst):
 src,dst=Path(src),Path(dst);expected=tree_manifest(src)
 if dst.is_symlink():raise RuntimeError('RELEASE_SYMLINK_REFUSED')
 if dst.exists():
  marker=dst/'T3_COMPONENT_MANIFEST.json'
  if not marker.is_file() or json.loads(marker.read_text())!=expected or tree_manifest(dst,('T3_COMPONENT_MANIFEST.json',))!=expected:raise RuntimeError('EXISTING_RELEASE_DIFFERENT_OR_LOCAL_CHANGE:'+str(dst))
  return 'REUSED_IDENTICAL'
 dst.parent.mkdir(parents=True,exist_ok=True)
 stage=Path(tempfile.mkdtemp(prefix='.'+dst.name+'-',dir=dst.parent))
 try:
  for p in src.iterdir():
   if p.name=='__pycache__':continue
   if p.is_dir():shutil.copytree(p,stage/p.name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
   else:shutil.copy2(p,stage/p.name)
  (stage/'T3_COMPONENT_MANIFEST.json').write_text(json.dumps(expected,indent=2)+'\n');os.replace(stage,dst)
 finally:
  if stage.exists():shutil.rmtree(stage)
 return 'INSTALLED_NEW'

def install(root,reports):
 if os.geteuid()==0:raise RuntimeError('NAO_EXECUTE_COM_SUDO')
 if sys.version_info<(3,10):raise RuntimeError('PYTHON_3_10_OR_NEWER_REQUIRED')
 if not Path('/mnt/sherlock-ssd').is_mount():raise RuntimeError('SSD_NAO_MONTADO')
 if not BASE.is_dir() or BASE.is_symlink():raise RuntimeError('BASE_AUSENTE_OU_SYMLINK')
 if shutil.disk_usage(BASE).free<8*1024**3:raise RuntimeError('MIN_8_GIB_FREE_REQUIRED')
 if not shutil.which('cargo'):raise RuntimeError('CARGO_AUSENTE')
 declared=json.loads((root/'PAYLOAD_SHA256.json').read_text()).get('files')
 if declared!=tree_manifest(root,('PAYLOAD_SHA256.json',)):raise RuntimeError('PAYLOAD_MANIFEST_MISMATCH')
 reports.mkdir(parents=True,exist_ok=False);env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
 print('[1/7] Testes Python: núcleo herdado, N2 T3, livros, contabilidade e segurança.',flush=True)
 for name in ('mp12','n2_mp12','d2','runner','perp'):
  run([sys.executable,'-B','-m','unittest','discover','-s',str(root/name/'tests'),'-v'],reports/(name+'_tests.log'),env,180)
 n1=BASE/'releases/r5d-n1-numeric-v1';expected_n1=json.loads((root/'mp12/EXPECTED_N1.json').read_text())
 for relative,h in expected_n1.items():
  p=n1/relative
  if not p.is_file() or p.is_symlink() or digest(p)!=h:raise RuntimeError('FROZEN_N1_MISSING_OR_CHANGED:'+str(p))
 print('BELIAL_T3_FIX4=TOKEN_PROGRAMS_VALIDATED; RPC_SELECTION_PROPAGATED=true; DECODERS=AMM_V4_CPMM_CLMM; CAPTURE_MINUTES=90; CAPTURE_SECONDS=5400; POOLS_REQUIRED=12',flush=True)
 # Diagnose saved bytes first, without treating a historical snapshot as approval.
 try:
  from audit_saved_tokens import audit
  saved=audit(BASE)
  (reports/'HISTORICAL_TOKEN_AUDIT.json').write_text(json.dumps(saved,indent=2)+'\n')
 except Exception as exc:
  print('FIX4_HISTORICAL_AUDIT_WARNING='+str(exc),flush=True)
 print('[2/7] Descoberta MP12 + RPC/WS mainnet aprovado: contas, histórico e 12 assinaturas de logs.',flush=True)
 pool_file=reports/'POOL_PREFLIGHT.json';market_file=reports/'PERP_MARKETS.json'
 run([sys.executable,'-B',str(root/'runner/tools/preflight_pools.py'),'--out',str(pool_file)],reports/'pool_preflight.log',env,900,stream=True)
 approved=json.loads(pool_file.read_text());print('RPC_APROVADO='+public_endpoint(approved['rpc'])+'; WS_APROVADO='+public_endpoint(approved['ws']),flush=True)
 print('[3/7] Descoberta pública dos mercados perp. Indisponíveis permanecem explícitos.',flush=True)
 run([sys.executable,'-B',str(root/'perp/tools/markets.py'),'--out',str(market_file)],reports/'perp_metadata.log',env,240)
 print('[4/7] Testes + compilação Rust release/offline. Sem instalar bibliotecas ou driver.',flush=True)
 target=BASE/'target-r5d-t3-mp12';env2=dict(env,CARGO_TARGET_DIR=str(target),CARGO_NET_OFFLINE='true')
 for command,log in [('test','rust_tests.log'),('build','rust_build.log')]:
  run(['cargo',command,'--release','--locked','--offline','--jobs','1','--manifest-path',str(root/'mp12/engine/Cargo.toml')],reports/log,env2,3600)
 for binary in ('belial-r5c2-live-profiler','belial-perp-feed'):
  if not (target/'release'/binary).is_file():raise RuntimeError('BINARY_MISSING:'+binary)
 print('[4A/7] Contratos dos 12 tokens e cofres: validação binária nativa Rust, sem rede.',flush=True)
 run([str(target/'release/belial-r5c2-live-profiler'),'--fix4-token-manifest',str(pool_file),
      '--output',str(reports/'RUST_TOKEN_CONTRACTS.json')],reports/'rust_token_contracts.log',env,60)
 print('[4B/7] Qualificação real de swaps recentes CPMM/CLMM: Rust x Python. Sem treino/backfill.',flush=True)
 run([sys.executable,'-B',str(root/'runner/tools/qualify_protocols.py'),'--manifest',str(pool_file),
      '--binary',str(target/'release/belial-r5c2-live-profiler'),'--out',str(reports/'protocol_qualification')],
      reports/'protocol_qualification.log',env,600)
 print('[5/7] Releases T3 isoladas e verificadas por SHA-256.',flush=True)
 installed={}
 with tempfile.TemporaryDirectory(prefix='belial-t3-stage-') as td:
  stage=Path(td);(stage/'bin').mkdir()
  for binary in ('belial-r5c2-live-profiler','belial-perp-feed'):shutil.copy2(target/'release'/binary,stage/'bin'/binary)
  shutil.copytree(root/'mp12/tools',stage/'tools');shutil.copy2(root/'mp12/EXPECTED_N1.json',stage/'EXPECTED_N1.json')
  inv=tree_manifest(stage);(stage/'INSTALLED_SHA256.json').write_text(json.dumps(inv,indent=2)+'\n')
  installed['r5d-mp12-live-v1-fix4-90m']=install_dir(stage,BASE/'releases/r5d-mp12-live-v1-fix4-90m')
 for folder,release in [('n2_mp12','r5d-n2-gpu-mp12-v1'),('perp','r5d-t3-perp-90m-fix4-v1'),('runner','r5d-t3-90m-mp12-ultra-v1-fix4')]:installed[release]=install_dir(root/folder,BASE/'releases'/release)
 backup=BASE/'.belial-backups'/('t3-90m-'+reports.name);backup.mkdir(parents=True,exist_ok=False)
 (backup/'INSTALL_STATE.json').write_text(json.dumps({'new_releases':installed,'old_releases_modified':False,'original_source_sha256':json.loads((root/'PROVENIENCIA_T2.json').read_text())['source_payload_sha256'],'reports':str(reports)},indent=2)+'\n')
 # No symlink or original files were replaced. Rollback is deliberately non-destructive.
 (backup/'ROLLBACK_T3_MP12.sh').write_text('#!/usr/bin/env bash\nset -euo pipefail\necho "T3 usa releases separadas. Nenhum arquivo T2/N1 foi substituído."\necho "Finalize o executor T3 com Ctrl+C. Evidências e versões antigas permanecem preservadas."\n')
 (backup/'ROLLBACK_T3_MP12.sh').chmod(0o700)
 print('[6/7] Autoteste OpenCL + benchmark obrigatório 8192 x 32 na sua GPU.',flush=True)
 code=("import sys,json;sys.path.insert(0,"+repr(str(BASE/'releases/r5d-n2-gpu-mp12-v1/tools'))+");from opencl_core import Core;from live import select_stage;c=Core('NVIDIA');t=c.self_test(512);rows=c.benchmark([[8192,32]],10,64);stage=select_stage(rows,20.0,[8192,32]);print(json.dumps({'device':c.info,'self_test':t,'benchmark':rows,'selected':stage},indent=2));c.close()")
 run([sys.executable,'-B','-c',code],reports/'gpu_preflight.log',env,180)
 print('[7/7] T3 prospectivo: 90 minutos (1h30) / 12 pools / PAPER perp / dashboard no navegador.',flush=True)
 print(json.dumps({'T3_INSTALL_OK':True,'REPORTS':str(reports),'BACKUP':str(backup),'MAIN_STRATEGY_MODIFIED':False,'SIGNAL_ENGINE_FEEDBACK':False,'TRANSACTIONS_SUBMITTED':False,'RELEASES':installed},indent=2),flush=True)
 return subprocess.call([sys.executable,'-B',str(BASE/'releases/r5d-t3-90m-mp12-ultra-v1-fix4/tools/run90m.py'),'--minutes',str(CAPTURE_MINUTES),'--pool-manifest',str(pool_file),'--markets',str(market_file)],env=env)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--payload',type=Path,required=True);a=ap.parse_args();root=a.payload.resolve();reports=BASE/'runtime/r5d-t3-90m-mp12-ultra/install-reports'/time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
 try:return install(root,reports)
 except Exception as e:
  print('BELIAL_T3_INSTALL_FAIL='+str(e),flush=True)
  if reports.is_dir():
   (reports/'FAILURE.json').write_text(json.dumps({'error':str(e),'orders':False,'at_ms':time.time_ns()//1000000},indent=2)+'\n')
   DESKTOP.mkdir(parents=True,exist_ok=True);out=DESKTOP/('BELIAL_T3_90M_FIX4_PREFLIGHT_FALHA_'+reports.name+'.zip')
   with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for p in reports.rglob('*'):
     if p.is_file():archive_file(z,p,p.relative_to(reports),[os.environ.get('BELIAL_SOLANA_RPC_URL',''),os.environ.get('BELIAL_SOLANA_WS_URL','')])
   print('FAILURE_ZIP='+str(out),flush=True)
  raise
if __name__=='__main__':raise SystemExit(main())
