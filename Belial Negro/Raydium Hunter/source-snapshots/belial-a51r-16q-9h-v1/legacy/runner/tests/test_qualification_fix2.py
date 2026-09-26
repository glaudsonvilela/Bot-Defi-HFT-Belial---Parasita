import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'runner/tools'))
import qualify_protocols as q
import protocol_fixtures as f

class QualificationFIX2(unittest.TestCase):
 def manifest(self,c):
  return {'ok':True,'rpc':'fixture','pools':[c['cfg']]+[dict(symbol='dummy'+str(i),owner='legacy-test') for i in range(11)]}
 def fake_call(self,c):
  def call(url,method,params):
   if method=='getSignaturesForAddress':return [{'signature':c['signature'],'slot':c['slot'],'err':None}]
   if method=='getTransaction':return c.get('tx')
   raise AssertionError('NO_SIGNING_OR_SEND_METHOD_ALLOWED:'+method)
  return call
 def fake_binary(self,*args,**kwargs):
  cmd=args[0];path=Path(cmd[cmd.index('--fix2-decode-file')+1]);out=Path(cmd[cmd.index('--output')+1]);cases=[json.loads(s) for s in path.read_text().splitlines()]
  # This mock verifies orchestration ONLY, not Rust execution.
  out.write_text(''.join(json.dumps(f.row_from_case(c))+'\n' for c in cases))
  return subprocess.CompletedProcess(cmd,0,'SYNTHETIC_MOCK_NOT_RUST','')
 def test_unapproved_manifest_cannot_qualify(self):
  with tempfile.TemporaryDirectory() as td:self.assertRaisesRegex(RuntimeError,'APPROVED',q.qualify,{'ok':False},'fixture',Path(td)/'probe')
 def test_empty_signature_history_is_not_success(self):
  c,_,_=f.clmm()
  with tempfile.TemporaryDirectory() as td,patch.object(q.time,'sleep'):
   self.assertRaisesRegex(RuntimeError,'NO_QUALIFIED_SWAP',q.qualify,self.manifest(c),'fixture',Path(td)/'probe',lambda *_:[])
   self.assertFalse(json.loads((Path(td)/'probe/REPORT.json').read_text())['ok'])
 def test_supported_case_is_independently_audited(self):
  c,_,_=f.clmm()
  with tempfile.TemporaryDirectory() as td,patch.object(q.subprocess,'run',side_effect=self.fake_binary) as binary,patch.object(q.time,'sleep'):
   report=q.qualify(self.manifest(c),'fixture',Path(td)/'probe',self.fake_call(c))
   self.assertTrue(report['ok']);self.assertFalse(report['scan_started']);self.assertFalse(report['n2_training']);self.assertFalse(report['orders'])
   self.assertEqual(report['rust_python_crosschecked'],1);self.assertIn('--fix2-decode-file',binary.call_args.args[0])
 def test_wrong_rust_price_never_approved(self):
  c,_,_=f.clmm()
  def bad(*args,**kwargs):
   result=self.fake_binary(*args,**kwargs);cmd=args[0];path=Path(cmd[-1]);row=json.loads(path.read_text());row['reference_post_price']=999;path.write_text(json.dumps(row)+'\n');return result
  with tempfile.TemporaryDirectory() as td,patch.object(q.subprocess,'run',side_effect=bad),patch.object(q.time,'sleep'):
   self.assertRaisesRegex(RuntimeError,'REFERENCE_MISMATCH',q.qualify,self.manifest(c),'fixture',Path(td)/'probe',self.fake_call(c))
 def test_decoder_failure_never_approved(self):
  c,_,_=f.clmm()
  with tempfile.TemporaryDirectory() as td,patch.object(q.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','bad decoder')),patch.object(q.time,'sleep'):
   self.assertRaisesRegex(RuntimeError,'RUST_DECODER_QUALIFICATION_FAILED',q.qualify,self.manifest(c),'fixture',Path(td)/'probe',self.fake_call(c))
 def test_wrong_tx_delta_blocks_before_rust(self):
  c,_,_=f.clmm();c['tx']['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='1'
  with tempfile.TemporaryDirectory() as td,patch.object(q.subprocess,'run') as binary,patch.object(q.time,'sleep'):
   self.assertRaisesRegex(RuntimeError,'NO_QUALIFIED_SWAP',q.qualify,self.manifest(c),'fixture',Path(td)/'probe',self.fake_call(c));binary.assert_not_called()
 def test_history_limit_and_no_rpc_send(self):
  c,_,_=f.clmm();calls=[];base=self.fake_call(c)
  def rpc(url,method,params):calls.append((method,params));return base(url,method,params)
  with tempfile.TemporaryDirectory() as td,patch.object(q.subprocess,'run',side_effect=self.fake_binary),patch.object(q.time,'sleep'):
   q.qualify(self.manifest(c),'fixture',Path(td)/'probe',rpc)
  self.assertEqual([m for m,_ in calls],['getSignaturesForAddress','getTransaction']);self.assertEqual(calls[0][1][1]['limit'],12)
 def test_qualification_precedes_session(self):
  s=(ROOT/'runner/tools/install_and_run.py').read_text();self.assertLess(s.index("'qualify_protocols.py'") if "'qualify_protocols.py'" in s else s.index('qualify_protocols.py'),s.index("print('[7/7]"))
 def test_rust_config_guard_refuses_changed_or_stale_parameters(self):
  src=(ROOT/'mp12/engine/src/raydium_protocols.rs').read_text();main=(ROOT/'mp12/engine/src/main.rs').read_text()
  self.assertIn('cpmm_fee_config_changed_session_invalidated',src);self.assertIn('Duration::from_secs(10)',src)
  self.assertIn('saturating_sub(checked)>15_000',main);self.assertIn('fee_config_last_verified_unix_ms',main)
 def test_causal_timestamp_after_rpc_and_cached_actor(self):
  s=(ROOT/'mp12/engine/src/main.rs').read_text();body=s[s.index('async fn pool_task('):s.index('async fn main()')]
  self.assertLess(body.index('raydium_protocols::fetch_tx'),body.index('let received_ns = now_ns();'))
  self.assertIn('if let Some(hint)=&event.actor_hint {return Ok(hint.clone());}',s)
 def test_no_new_rust_dependencies_and_original_perp_transport(self):
  import hashlib
  d=json.loads((ROOT/'PRESERVED_T3_COMPONENTS.json').read_text())['files']
  for path in ['mp12/engine/Cargo.lock','mp12/engine/Cargo.toml','mp12/engine/src/bin/belial-perp-feed.rs']:
   self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),d[path])

if __name__=='__main__':unittest.main()
