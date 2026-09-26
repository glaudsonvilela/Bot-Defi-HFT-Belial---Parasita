"""Synthetic contract regressions. No fixtures here are claimed as live PUMP data."""
import base64, copy, contextlib, hashlib, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'runner/tools'))
import token_capabilities as tc
import preflight_pools as p
import protocol_reference as pr
import protocol_fixtures as f
import audit_saved_tokens as saved
from test_preflight_fix1 import fixture, mint_account

PUMP=p.CANONICAL_MINTS['PUMP']


def encode(raw, program=tc.TOKEN_2022):
 return {'owner':program,'executable':False,'data':[base64.b64encode(raw).decode(),'base64']}

def raw(value):return bytearray(base64.b64decode(value['data'][0]))

def tlv(tag, payload):return tag.to_bytes(2,'little')+len(payload).to_bytes(2,'little')+payload

def text(s):
 b=s.encode();return len(b).to_bytes(4,'little')+b

def metadata(mint=PUMP):
 return bytes(32)+p.pubkey_bytes(mint)+text('PUMP synthetic fixture')+text('PUMP')+text('fixture:offline')+bytes(4)

def mint22(mint=PUMP, exts=None):
 b=raw(mint_account());b[36:44]=(10**15).to_bytes(8,'little')
 if exts is None:exts=[(18,bytes(32)+p.pubkey_bytes(mint)),(19,metadata(mint))]
 if exts:
  b.extend(bytes(165-len(b))+b'\1')
  for tag,payload in exts:b.extend(tlv(tag,payload))
 return encode(b)

def vault22(mint=PUMP,extensions=((7,b''),)):
 b=raw(f.token_vault(mint));b.extend(b'\2')
 for tag,payload in extensions:b.extend(tlv(tag,payload))
 return encode(b)

def world(assignments=None):
 targets,accounts,_=fixture();targets=copy.deepcopy(targets);accounts=copy.deepcopy(accounts)
 assignments=assignments or {'PUMP':('CLMM',tc.TOKEN_2022)}
 for sym,(kind,program) in assignments.items():
  index=p.SYMBOLS.index(sym);_,pool,mint=targets[index]
  accounts[mint]=mint22(mint) if program==tc.TOKEN_2022 else mint_account()
  if kind=='AMM_V4':continue
  cfg,account,conf=f.pool_config(kind,True);state=raw(account)
  v0=f.key(index+70);v1=f.key(index+90);config=f.key(index+110)
  fields=([(8,config),(72,v0),(104,v1),(168,mint),(200,p.WSOL),(232,program),(264,tc.TOKEN)]
    if kind=='CPMM' else [(9,config),(73,mint),(105,p.WSOL),(137,v0),(169,v1)])
  for offset,key in fields:state[offset:offset+32]=p.pubkey_bytes(key)
  accounts[pool]=encode(state,p.CPMM if kind=='CPMM' else p.CLMM)
  accounts[v0]=vault22(mint) if program==tc.TOKEN_2022 else f.token_vault(mint)
  accounts[v1]=f.token_vault(p.WSOL)
  if kind=='CPMM':accounts[config]=conf
 def rpc(_url,keys,client=None):return [accounts.get(k) for k in keys],123456
 return targets,accounts,rpc

def validate(targets,rpc,folder=None):
 with patch.object(p,'rpc',side_effect=rpc),contextlib.redirect_stdout(io.StringIO()):
  return p.validate_targets('https://fixture.invalid',targets,{}, {'resolved':{}},folder)


class BinaryTokenCapabilities(unittest.TestCase):
 def test_legacy82(self):self.assertEqual(tc.validate_mint(mint_account(),PUMP)['family'],'SPL_TOKEN')
 def test_t22_base82(self):self.assertEqual(tc.validate_mint(mint22(exts=[]),PUMP)['family'],'TOKEN_2022')
 def test_t22_metadata(self):self.assertEqual([e['name'] for e in tc.validate_mint(mint22(),PUMP)['extensions']],['MetadataPointer','TokenMetadata'])
 def test_program_not_inferred_from_ticker(self):
  v=mint22();v['owner']='UnapprovedProgram';self.assertRaisesRegex(RuntimeError,'owner=.*UnapprovedProgram',tc.validate_mint,v,PUMP)
 def test_legacy_rejects_extended_account(self):
  v=mint22();v['owner']=tc.TOKEN;self.assertRaisesRegex(RuntimeError,'LEGACY_TOKEN',tc.validate_mint,v,PUMP)
 def test_mint_missing(self):self.assertRaisesRegex(RuntimeError,'MINT_OWNER',tc.validate_mint,None,PUMP)
 def test_executable_rejected(self):
  v=mint22();v['executable']=True;self.assertRaisesRegex(RuntimeError,'MINT_OWNER',tc.validate_mint,v,PUMP)
 def test_missing_executable_flag_rejected(self):
  v=mint22();v.pop('executable');self.assertRaises(RuntimeError,tc.validate_mint,v,PUMP)
 def test_non_base64_encoding(self):
  v=mint22();v['data'][1]='base64+zstd';self.assertRaisesRegex(RuntimeError,'ENCODING',tc.validate_mint,v,PUMP)
 def test_invalid_base64(self):
  v=mint22();v['data'][0]='!!!!';self.assertRaisesRegex(RuntimeError,'BASE64',tc.validate_mint,v,PUMP)
 def test_short_mint(self):self.assertRaises(RuntimeError,tc.validate_mint,encode(bytes(81)),PUMP)
 def test_uninitialized_mint(self):
  b=raw(mint22());b[45]=0;self.assertRaisesRegex(RuntimeError,'INITIALIZATION',tc.validate_mint,encode(b),PUMP)
 def test_excessive_decimals(self):
  b=raw(mint22());b[44]=19;self.assertRaisesRegex(RuntimeError,'DECIMALS',tc.validate_mint,encode(b),PUMP)
 def test_invalid_authority_option(self):
  b=raw(mint22());b[0]=2;self.assertRaisesRegex(RuntimeError,'COPTION',tc.validate_mint,encode(b),PUMP)
 def test_multisig_never_mint(self):
  b=raw(mint_account());b.extend(bytes(355-82));self.assertRaisesRegex(RuntimeError,'MULTISIG',tc.validate_mint,encode(b),PUMP)
 def test_bad_mint_padding(self):
  b=raw(mint22());b[82]=1;self.assertRaisesRegex(RuntimeError,'PADDING',tc.validate_mint,encode(b),PUMP)
 def test_account_type_vault_not_mint(self):
  b=raw(mint22());b[165]=2;self.assertRaisesRegex(RuntimeError,'ACCOUNT_TYPE',tc.validate_mint,encode(b),PUMP)
 def test_extended_too_short(self):
  b=raw(mint_account())+bytes(83);self.assertRaisesRegex(RuntimeError,'TYPE_OR_LENGTH',tc.validate_mint,encode(b),PUMP)
 def test_extension_truncation(self):
  b=raw(mint22());b.pop();self.assertRaisesRegex(RuntimeError,'OUT_OF_BOUNDS',tc.validate_mint,encode(b),PUMP)
 def test_duplicate_extension(self):
  self.assertRaisesRegex(RuntimeError,'DUPLICATE',tc.validate_mint,mint22(exts=[(18,bytes(64)),(18,bytes(64))]),PUMP)
 def test_unknown_extension(self):self.assertRaisesRegex(RuntimeError,'UNKNOWN_65535',tc.validate_mint,mint22(exts=[(65535,b'')]),PUMP)
 def test_wrong_fixed_extension_size(self):self.assertRaisesRegex(RuntimeError,'EXTENSION_LENGTH',tc.validate_mint,mint22(exts=[(18,bytes(63))]),PUMP)
 def test_zero_padding_allowed(self):
  b=raw(mint22())+bytes(12);self.assertTrue(tc.validate_mint(encode(b),PUMP)['ok'])
 def test_terminator_cannot_hide_more_data(self):
  b=raw(mint22())+bytes(4)+b'X';self.assertRaisesRegex(RuntimeError,'TERMINATOR',tc.validate_mint,encode(b),PUMP)
 def test_mint_extension_on_vault_rejected(self):
  self.assertRaisesRegex(RuntimeError,'WRONG_ACCOUNT_TYPE',tc.validate_vault,vault22(extensions=[(18,bytes(64))]),PUMP,tc.TOKEN_2022)
 def test_vault_extension_on_mint_rejected(self):self.assertRaisesRegex(RuntimeError,'WRONG_ACCOUNT_TYPE',tc.validate_mint,mint22(exts=[(7,b'')]),PUMP)
 def test_transfer_fee_even_zero_rejected(self):self.assertRaisesRegex(RuntimeError,'TransferFeeConfig',tc.validate_mint,mint22(exts=[(1,bytes(108))]),PUMP)
 def test_transfer_hook_rejected(self):self.assertRaisesRegex(RuntimeError,'TransferHook',tc.validate_mint,mint22(exts=[(14,bytes(64))]),PUMP)
 def test_interest_scaled_confidential_and_pause_not_silently_ignored(self):
  for tag in [4,10,25,26]:
   with self.subTest(tag=tag):self.assertRaisesRegex(RuntimeError,'UNSUPPORTED',tc.validate_mint,mint22(exts=[(tag,b'')]),PUMP)
 def test_metadata_wrong_mint(self):
  self.assertRaisesRegex(RuntimeError,'METADATA_MINT',tc.validate_mint,mint22(exts=[(19,metadata(p.CANONICAL_MINTS['BONK']))]),PUMP)
 def test_metadata_invalid_utf8(self):
  data=bytearray(metadata());data[68]=255;self.assertRaisesRegex(RuntimeError,'UTF8',tc.validate_mint,mint22(exts=[(19,data)]),PUMP)
 def test_metadata_unbounded_string_length_rejected(self):
  data=bytearray(metadata());data[64:68]=(2**32-1).to_bytes(4,'little');self.assertRaisesRegex(RuntimeError,'STRING_LENGTH',tc.validate_mint,mint22(exts=[(19,data)]),PUMP)
 def test_default_frozen_not_approved(self):self.assertRaisesRegex(RuntimeError,'DEFAULT_ACCOUNT_STATE',tc.validate_mint,mint22(exts=[(6,b'\2')]),PUMP)
 def test_default_initialized(self):self.assertTrue(tc.validate_mint(mint22(exts=[(6,b'\1')]),PUMP)['ok'])
 def test_authorities_are_disclosed_not_safety_rated(self):
  b=raw(mint22());b[0]=1;b[46]=1;got=tc.validate_mint(encode(b),PUMP)
  self.assertTrue(got['mint_authority_present']);self.assertTrue(got['freeze_authority_present']);self.assertIn('NOT_TOKEN_RISK_APPROVAL',got['scope'])
 def test_vault_immutable_owner(self):self.assertEqual(tc.validate_vault(vault22(),PUMP,tc.TOKEN_2022)['extensions'][0]['name'],'ImmutableOwner')
 def test_vault_same_program_required(self):self.assertRaisesRegex(RuntimeError,'PROGRAM_MISMATCH',tc.validate_vault,vault22(),PUMP,tc.TOKEN)
 def test_vault_wrong_mint(self):self.assertRaisesRegex(RuntimeError,'MINT_OR_STATE',tc.validate_vault,vault22(),p.WSOL,tc.TOKEN_2022)
 def test_vault_frozen(self):
  b=raw(vault22());b[108]=2;self.assertRaisesRegex(RuntimeError,'STATE',tc.validate_vault,encode(b),PUMP,tc.TOKEN_2022)
 def test_vault_integer_amount_not_ui_string(self):
  b=raw(vault22());b[64:72]=(2**64-1).to_bytes(8,'little');self.assertEqual(tc.validate_vault(encode(b),PUMP,tc.TOKEN_2022)['amount_raw'],str(2**64-1))
 def test_vault_false_bool_rejected(self):
  self.assertRaisesRegex(RuntimeError,'BOOL',tc.validate_vault,vault22(extensions=[(8,b'\2')]),PUMP,tc.TOKEN_2022)
 def test_supply_integer_exact(self):self.assertEqual(tc.validate_mint(mint22(),PUMP)['supply_raw'],str(10**15))
 def test_hash_is_exact_raw_account_bytes(self):self.assertEqual(tc.validate_mint(mint22(),PUMP)['bytes_sha256'],hashlib.sha256(raw(mint22())).hexdigest())


class TwelvePoolContracts(unittest.TestCase):
 def test_reproduces_legacy_owner_gate_with_synthetic_pump_t22(self):
  value=mint22()
  def old_gate():
   if value['owner']!=tc.TOKEN:raise RuntimeError('MINT_OWNER_UNSUPPORTED:'+PUMP)
  self.assertRaisesRegex(RuntimeError,'MINT_OWNER_UNSUPPORTED:'+PUMP,old_gate)
  self.assertTrue(tc.validate_mint(value,PUMP)['ok'])
 def test_twelve_including_pump_clmm_token2022_all_stages(self):
  targets,_,rpc=world();report=validate(targets,rpc)
  self.assertEqual(len(report['pools']),12);self.assertEqual([r['symbol'] for r in report['pools']],list(p.SYMBOLS))
  pump=report['pools'][8];self.assertEqual(pump['coin_token_program'],tc.TOKEN_2022);self.assertTrue(pump['coin_vault_capabilities']['ok']);tc.verify_manifest(report)
 def test_cpmm_and_clmm_can_each_mix_legacy_and_t22(self):
  for kind in ['CPMM','CLMM']:
   targets,_,rpc=world({'PUMP':(kind,tc.TOKEN_2022),'PENGU':(kind,tc.TOKEN)})
   with self.subTest(kind=kind):tc.verify_manifest(validate(targets,rpc))
 def test_cpmm_claimed_program_must_match_real_mint_owner(self):
  targets,accounts,rpc=world({'PUMP':('CPMM',tc.TOKEN_2022)})
  b=raw(accounts[targets[8][1]]);b[232:264]=p.pubkey_bytes(tc.TOKEN);accounts[targets[8][1]]=encode(b,p.CPMM)
  self.assertRaisesRegex(RuntimeError,'CPMM_POOL_MINT_PROGRAM_MISMATCH',validate,targets,rpc)
 def test_legacy_amm_does_not_gain_unsupported_token2022(self):
  targets,_,rpc=world({'PUMP':('AMM_V4',tc.TOKEN_2022)});self.assertRaisesRegex(RuntimeError,'LEGACY_AMM_REQUIRES',validate,targets,rpc)
 def test_missing_token2022_vault_support_no_longer_second_surprise(self):
  targets,accounts,rpc=world();report=validate(targets,rpc);self.assertEqual(report['pools'][8]['coin_vault_capabilities']['account_len'],170)
 def test_all_bad_mints_reported_together(self):
  targets,accounts,rpc=world();accounts[PUMP]['owner']='BadPumpOwner';accounts[p.CANONICAL_MINTS['BONK']]['owner']='BadBonkOwner'
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaises(RuntimeError) as caught:validate(targets,rpc,Path(td))
   self.assertIn('BadPumpOwner',str(caught.exception));self.assertIn('BadBonkOwner',str(caught.exception))
   matrix=json.loads((Path(td)/'CAPABILITY_MATRIX.json').read_text());self.assertEqual(len(matrix['pools']),12);self.assertFalse(matrix['ok']);self.assertFalse(matrix['scan_started'])
   self.assertTrue(next(r for r in matrix['pools'] if r['symbol']=='PYTH')['mint_checked'])
 def test_mint_errors_do_not_hide_vault_errors(self):
  targets,accounts,rpc=world({'PUMP':('CLMM',tc.TOKEN_2022),'PENGU':('CPMM',tc.TOKEN)})
  accounts[PUMP]['owner']='Unknown';accounts[f.key(7+70)]['owner']='WrongVaultProgram'
  with self.assertRaises(RuntimeError) as caught:validate(targets,rpc)
  self.assertIn('MINT_OWNER',str(caught.exception));self.assertIn('VAULT_OWNER',str(caught.exception))
 def test_unknown_extensions_prevent_session(self):
  targets,accounts,rpc=world();accounts[PUMP]=mint22(exts=[(14,bytes(64))]);self.assertRaisesRegex(RuntimeError,'TransferHook',validate,targets,rpc)
 def test_manifest_rejects_old_preflight(self):self.assertRaisesRegex(RuntimeError,'MANIFEST_REQUIRED',tc.verify_manifest,{'ok':True,'pools':[{}]*12})
 def test_manifest_bytes_tamper_rejected(self):
  t,_,rpc=world();d=validate(t,rpc);d['pools'][8]['coin_mint_capabilities']['decimals']=9;self.assertRaisesRegex(RuntimeError,'EVIDENCE_CHANGED',tc.verify_manifest,d)
 def test_manifest_vault_tamper_rejected(self):
  t,_,rpc=world();d=validate(t,rpc);d['pools'][8]['coin_vault_data_b64']=base64.b64encode(bytes(165)).decode();self.assertRaises(RuntimeError,tc.verify_manifest,d)
 def test_exact_token_keys_preserved(self):
  t,_,rpc=world();d=validate(t,rpc);self.assertEqual([x['meme_mint'] for x in d['pools']],[x[2] for x in t]);self.assertEqual([x['pool'] for x in d['pools'][:6]],[x[1] for x in p.ORIGINAL])
 def test_rpc_call_count_not_increased_for_token_parsing(self):
  t,_,rpc=world();calls=[]
  def record(url,keys,client=None):calls.append(keys);return rpc(url,keys,client)
  validate(t,record);self.assertEqual(len(calls),3)
 def test_tx_balance_program_if_present_checked(self):
  case,_,_=f.clmm();d=pr.decode_event(base64.b64decode(case['event_b64']),case['cfg']);case['tx']['meta']['preTokenBalances'][0]['programId']='FakeTokenProgram'
  self.assertRaisesRegex(ValueError,'PROGRAM_MISMATCH',pr.tx_vaults,case['tx'],case['cfg'],d)


class SavedDiagnostics(unittest.TestCase):
 def write(self,base,value):
  path=Path(base)/'runtime/r5d-t3-90m-mp12-ultra/install-reports/20260925T111147Z/pool_metadata_fix2/rpc_fix3/candidate_1/RPC_MINT_ACCOUNTS.json'
  path.parent.mkdir(parents=True);path.write_text(json.dumps({'addresses':[PUMP],'accounts':[value],'slot':123}));return path
 def test_reads_old_snapshot_without_network(self):
  with tempfile.TemporaryDirectory() as td:
   self.write(td,mint22())
   with patch('urllib.request.urlopen',side_effect=AssertionError('NO NETWORK')),contextlib.redirect_stdout(io.StringIO()):result=saved.audit(Path(td))
   self.assertEqual(result['tokens'][0]['owner'],tc.TOKEN_2022);self.assertFalse(result['scan_started']);self.assertIn('NOT_LIVE_APPROVAL',result['role'])
 def test_missing_snapshot_does_not_claim_approved(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):self.assertEqual(saved.audit(Path(td))['status'],'NO_SAVED_MINT_SNAPSHOT')
 def test_unknown_actual_owner_preserved(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
   value=mint22();value['owner']='RecordedUnknownOwner';self.write(td,value);result=saved.audit(Path(td))
   self.assertEqual(result['tokens'][0]['owner'],'RecordedUnknownOwner');self.assertEqual(result['tokens'][0]['status'],'BLOCKED')
 def test_does_not_modify_source(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
   path=self.write(td,mint22());before=path.read_bytes();saved.audit(Path(td));self.assertEqual(path.read_bytes(),before)
 def test_symlink_source_refused(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
   path=self.write(td,mint22());other=Path(td)/'other.json';path.rename(other);path.symlink_to(other)
   self.assertEqual(saved.audit(Path(td))['status'],'NO_SAVED_MINT_SNAPSHOT')
 def test_invalid_schema_no_approval(self):
  with tempfile.TemporaryDirectory() as td:
   path=self.write(td,mint22());path.write_text('{}');self.assertRaisesRegex(RuntimeError,'SCHEMA',saved.audit,Path(td))
 def test_history_observation_not_marked_capture_ok(self):
  with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
   self.write(td,mint22());result=saved.audit(Path(td));self.assertNotIn('ok',result);self.assertFalse(result['orders'])


class FullPathSourceContracts(unittest.TestCase):
 def test_both_python_and_rust_have_same_program_and_revision(self):
  src=(ROOT/'mp12/engine/src/token_programs.rs').read_text()
  for val in [tc.TOKEN,tc.TOKEN_2022,tc.REVISION]:self.assertIn(val,src)
 def test_live_requires_revision_before_starting_processes(self):
  runner=(ROOT/'runner/tools/run90m.py').read_text();self.assertLess(runner.index('verify_manifest(selected_manifest)'),runner.index('old=old_states()'))
  src=(ROOT/'mp12/engine/src/main.rs').read_text();self.assertIn('FIX4 token preflight required',src)
 def test_cpmm_rust_does_not_hardcode_single_token_program(self):
  src=(ROOT/'mp12/engine/src/raydium_protocols.rs').read_text();self.assertNotIn('key(data,232)?!=TOKEN',src);self.assertIn('token_programs::verify_cfg',src)
 def test_nonzero_transfer_fee_event_guard_stays(self):
  src=(ROOT/'mp12/engine/src/raydium_protocols.rs').read_text();self.assertEqual(src.count('return Err("TRANSFER_FEE_UNSUPPORTED"'),2)
 def test_native_token_manifest_check_is_before_history_qualification(self):
  src=(ROOT/'runner/tools/install_and_run.py').read_text();self.assertLess(src.index('--fix4-token-manifest'),src.index('qualify_protocols.py'))

if __name__=='__main__':unittest.main()
