import base64,copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from a1.common import ROOT,LEGACY,digest,read_json
sys.path.insert(0,str(LEGACY/'runner/tests'))
from a1.spot_validation import validate_asset,validate_batch,pf,tc
import test_token_capabilities_fix4 as f
from a1.sensor_worker import classify
from a1 import build_worker

class IsolatedValidation(unittest.TestCase):
 def setUp(self):self.targets,self.accounts,self.rpc=f.world()
 def test_pump_transfer_hook_stays_raw_other_eleven_qualify_contract(self):
  self.accounts[pf.CANONICAL_MINTS['PUMP']]=f.mint22(exts=[(14,bytes(64))])
  with tempfile.TemporaryDirectory() as td,patch.object(pf,'rpc',side_effect=self.rpc):
   class Client:url='https://fixture.invalid'
   rows,caps,slot=validate_batch(self.targets,Client(),Path(td))
   self.assertEqual(len(rows),11);self.assertEqual(len(caps),12);self.assertEqual(caps['PUMP']['state'],'RAW_ONLY');self.assertFalse(caps['PUMP']['qualified']);self.assertFalse(caps['PUMP']['price']);self.assertIn('TransferHook',caps['PUMP']['reason'])
   self.assertTrue(caps['FARTCOIN']['identity']);self.assertFalse(caps['FARTCOIN']['qualified'])
 def test_hook_policy_not_modified(self):
  with self.assertRaisesRegex(RuntimeError,'TransferHook'):tc.validate_mint(f.mint22(exts=[(14,bytes(64))]),pf.CANONICAL_MINTS['PUMP'])
 def test_invalid_pool_identity_never_raw_approved(self):
  target=self.targets[0];account=copy.deepcopy(self.accounts[target[1]]);account['owner']='wrong'
  row,cap=validate_asset(target,account,self.accounts,self.accounts);self.assertIsNone(row);self.assertFalse(cap['identity']);self.assertFalse(cap['raw_capture'])
 def test_missing_mint_quarantines_local_contract_only(self):
  target=self.targets[0];del self.accounts[target[2]];row,cap=validate_asset(target,self.accounts[target[1]],self.accounts,self.accounts);self.assertIsNone(row);self.assertFalse(cap['qualified'])
 def test_amm_v4_preserved_identity_and_contract(self):
  target=self.targets[0];row,cap=validate_asset(target,self.accounts[target[1]],self.accounts,self.accounts)
  self.assertEqual(row['token_contract_revision'],tc.REVISION);self.assertEqual(row['coin_token_program'],tc.TOKEN);self.assertEqual(cap['state'],'CONTRACT_VERIFIED')
 def test_compatible_token2022_metadata_not_blanket_rejected(self):
  target=next(t for t in self.targets if t[0]=='PUMP');row,cap=validate_asset(target,self.accounts[target[1]],self.accounts,self.accounts)
  self.assertIsNotNone(row);self.assertEqual(row['coin_token_program'],tc.TOKEN_2022);self.assertFalse(cap['qualified'])
 def test_cpmm_supported_with_actual_program(self):
  targets,accounts,rpc=f.world({'PUMP':('CPMM',tc.TOKEN_2022)})
  t=next(t for t in targets if t[0]=='PUMP');row,cap=validate_asset(t,accounts[t[1]],accounts,accounts);self.assertIsNotNone(row);self.assertIn('protocol_fee_rate',row)
 def test_wrong_decimals_rejected(self):
  t=self.targets[0];raw=f.raw(self.accounts[t[2]]);raw[44]=1;self.accounts[t[2]]=f.encode(raw,tc.TOKEN)
  row,cap=validate_asset(t,self.accounts[t[1]],self.accounts,self.accounts);self.assertIsNone(row);self.assertIn('DECIMALS',cap['reason'])
 def test_rpc_failure_remains_explicit_no_empty_approval(self):
  with tempfile.TemporaryDirectory() as td,patch.object(pf,'rpc',side_effect=RuntimeError('403')):
   class Client:url='https://fixture.invalid'
   self.assertRaisesRegex(RuntimeError,'403',validate_batch,self.targets,Client(),Path(td))

class SensorHealth(unittest.TestCase):
 def setUp(self):self.d={'engine_status':'RUNNING','rpc_errors':0,'dropped_events':0,'updated_unix_ms':100000};self.h={'state':'ACTIVE','generation':0,'at_ms':100000}
 def test_waits_for_ack(self):self.assertEqual(classify(self.d,dict(self.h,state='WAITING_ACK'),100000)[0],'STARTING')
 def test_active_after_native_ack(self):self.assertEqual(classify(self.d,self.h,100001)[0],'ACTIVE')
 def test_heartbeat_alone_is_not_rpc_health(self):self.d['rpc_errors']=1;self.assertEqual(classify(self.d,self.h,100001)[0],'QUARANTINED')
 def test_dropped_event_quarantines(self):self.d['dropped_events']=1;self.assertEqual(classify(self.d,self.h,100001)[0],'QUARANTINED')
 def test_disconnect_quarantines(self):self.assertEqual(classify(self.d,dict(self.h,state='DISCONNECTED'),100001)[0],'QUARANTINED')
 def test_reconnect_is_not_same_cohort_continuity(self):self.assertEqual(classify(self.d,dict(self.h,generation=1),100001)[0],'QUARANTINED')
 def test_socket_stale(self):self.d['updated_unix_ms']=150001;self.assertEqual(classify(self.d,self.h,150001)[0],'STALE')
 def test_native_writer_stale(self):self.h['at_ms']=106000;self.assertEqual(classify(self.d,self.h,106000)[0],'STALE')
 def test_no_dashboard_no_fabricated_active(self):self.assertEqual(classify({},self.h,100001)[0],'STARTING')

class BuildAndPreservation(unittest.TestCase):
 def test_science_files_byte_identical(self):
  for name,want in read_json(ROOT/'LEGACY_SHA256.json').items():self.assertEqual(digest(LEGACY/name),want,name)
 def test_no_new_rust_dependency(self):
  for name in ('Cargo.toml','Cargo.lock'):self.assertEqual(digest(ROOT/'engine'/name),digest(LEGACY/'mp12/engine'/name))
 def test_hook_decoder_files_unchanged(self):
  for name in ('m2_core.rs','raydium_protocols.rs','token_programs.rs'):self.assertEqual(digest(ROOT/'engine/src'/name),digest(LEGACY/'mp12/engine/src'/name))
 def test_no_cargo_reports_unavailable_not_pass(self):
  with tempfile.TemporaryDirectory() as td,patch.object(build_worker.shutil,'which',return_value=None):
   build_worker.run(Path(td),Path(td)/'target');r=read_json(Path(td)/'build/RESULTS.json');self.assertEqual(set(r),{'perp','raw','spot'});self.assertTrue(all(not v['ok'] and not v['native_tests_executed'] for v in r.values()))
 def test_binary_gates_are_independent(self):self.assertEqual([r[0] for r in build_worker.BINS],['perp','raw','spot'])
 def test_native_single_source_scope_and_no_unwrap_missing_pool(self):
  src=(ROOT/'engine/src/main.rs').read_text();self.assertIn('if isolated {1} else {12}',src);self.assertIn('let Some(pool) = guard.pools.get(symbol) else { continue; }',src);self.assertIn('subscription_rejected',src)
 def test_native_raw_recorder_is_not_decoder(self):
  src=(ROOT/'engine/src/bin/belial-spot-raw.rs').read_text();self.assertIn('"price_validated":false',src);self.assertNotIn('sendTransaction',src);self.assertNotIn('decode_event(',src)
if __name__=='__main__':unittest.main()
