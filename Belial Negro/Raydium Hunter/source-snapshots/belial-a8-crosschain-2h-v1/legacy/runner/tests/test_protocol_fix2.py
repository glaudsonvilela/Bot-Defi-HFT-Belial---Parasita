import base64,copy,hashlib,json,math,sys,tempfile,unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'runner/tools'))
import preflight_pools as p
import protocol_reference as r
import protocol_fixtures as f
from mp12_contract import Mark
import run90m

class ActualFailureRegression(unittest.TestCase):
 def response(self,symbol):return json.loads((Path(__file__).parent/'fixtures/fix1_actual_metadata'/f'{symbol}_list-v2_1.json').read_text())['response']
 def test_actual_penguin_old_reject_new_cpmm(self):
  d=self.response('PENGU');mint=p.CANONICAL_MINTS['PENGU']
  self.assertRaises(RuntimeError,p.choose_legacy,d,mint)
  row=p.choose_supported(d,mint);self.assertEqual(row['programId'],p.CPMM);self.assertEqual(row['id'],'Ha4SGpJP9mBNcmvYDFCJD7bGbmHTx6mpzcC9XuqsikHJ')
 def test_actual_grass_old_reject_new_cpmm(self):
  d=self.response('GRASS');mint=p.CANONICAL_MINTS['GRASS']
  self.assertRaises(RuntimeError,p.choose_legacy,d,mint)
  self.assertEqual(p.choose_supported(d,mint)['id'],'3GsB3iPBGkbPedYJmAkV6E8ywZeuMBzBEqwKsYbYZhtG')
 def test_actual_useless_avoids_52_dollar_pool(self):
  d=self.response('USELESS');m=p.CANONICAL_MINTS['USELESS']
  self.assertEqual(p.choose_legacy(d,m)['tvl'],52.24)
  selected=p.choose_supported(d,m);self.assertEqual(selected['tvl'],5661165.3);self.assertEqual(selected['programId'],p.CPMM)
 def test_actual_pyth_rank_all_supported(self):
  m=p.CANONICAL_MINTS['PYTH'];d=self.response('PYTH')
  self.assertGreater(p.choose_supported(d,m)['tvl'],p.choose_legacy(d,m)['tvl'])
 def test_actual_pump_standard_empty_not_all_families(self):
  self.assertEqual(p.pool_page(self.response('PUMP'))[0],[])
  self.assertNotIn('poolType=',p.discovery_url(p.CANONICAL_MINTS['PUMP'],'list-v2'))
  self.assertIn('poolType=all',p.discovery_url(p.CANONICAL_MINTS['PUMP'],'legacy'))
 def test_captured_metadata_hashes_match_input_zip(self):
  folder=Path(__file__).parent/'fixtures/fix1_actual_metadata';d=json.loads((folder/'PROVENANCE.json').read_text())
  self.assertEqual(d['sha256'],'641dcbbaeaf09cfbd09b2733ecb7a61365fc48693091e61169bb272200c99e02')
  for path,want in d['files'].items():self.assertEqual(hashlib.sha256((folder/path).read_bytes()).hexdigest(),want)
 def test_clmm_candidate_selected_with_concentrated_type_fixture(self):
  cfg,_,_=f.pool_config('CLMM');row=dict(id=cfg['pool'],programId=p.CLMM,type='Concentrated',tvl=100000,mintA={'address':cfg['coin_mint']},mintB={'address':cfg['pc_mint']})
  d={'success':True,'data':{'data':[row]}}
  with patch.object(p,'http',return_value=d):selected,info=p.resolve_pool('PENGU',cfg['meme_mint'])
  self.assertEqual(info['pool_kind'],'CLMM');self.assertEqual(selected['id'],row['id'])
 def test_zero_liquidity_never_selected(self):
  d=self.response('GRASS')
  for row in d['data']['data']:row['tvl']=0
  self.assertRaises(RuntimeError,p.choose_supported,d,p.CANONICAL_MINTS['GRASS'])

class PoolLayouts(unittest.TestCase):
 def test_cpmm_637_and_both_orientations(self):
  for m0 in [True,False]:
   cfg,account,_=f.pool_config('CPMM',m0);row=p.validate_pool(cfg['symbol'],cfg['pool'],cfg['meme_mint'],account)
   for name in ['coin_mint','pc_mint','coin_vault','pc_vault','amm_config','coin_decimals','pc_decimals']:self.assertEqual(row[name],cfg[name])
 def test_clmm_sqrt_offset_and_both_orientations(self):
  for m0 in [True,False]:
   cfg,account,_=f.pool_config('CLMM',m0);row=p.validate_pool(cfg['symbol'],cfg['pool'],cfg['meme_mint'],account)
   self.assertEqual(row['pool_kind'],'CLMM');self.assertEqual(row['coin_decimals'],cfg['coin_decimals'])
 def bad(self,kind,offset,value,error):
  cfg,account,_=f.pool_config(kind);raw=bytearray(base64.b64decode(account['data'][0]));raw[offset]=value;account['data'][0]=base64.b64encode(raw).decode()
  self.assertRaisesRegex(RuntimeError,error,p.validate_pool,cfg['symbol'],cfg['pool'],cfg['meme_mint'],account)
 def test_cpmm_discriminator_rejected(self):self.bad('CPMM',0,0,'DISCRIMINATOR')
 def test_clmm_discriminator_rejected(self):self.bad('CLMM',0,0,'DISCRIMINATOR')
 def test_cpmm_swaps_disabled(self):self.bad('CPMM',329,4,'SWAPS_DISABLED')
 def test_clmm_swaps_disabled(self):self.bad('CLMM',389,16,'SWAPS_DISABLED')
 def test_cpmm_unknown_token_program_rejected(self):self.bad('CPMM',232,0,'TOKEN_PROGRAM')
 def test_clmm_zero_sqrt_rejected(self):self.bad('CLMM',261,0,'SQRT_PRICE_INVALID')
 def test_unknown_sizes_rejected(self):
  for k in ['CPMM','CLMM']:
   c,a,_=f.pool_config(k);a['data'][0]=base64.b64encode(base64.b64decode(a['data'][0])+b'0').decode()
   self.assertRaisesRegex(RuntimeError,'LAYOUT',p.validate_pool,c['symbol'],c['pool'],c['meme_mint'],a)
 def test_config_fees_and_hash(self):
  _,_,a=f.pool_config();v=p.validate_cpmm_config(a)
  self.assertEqual((v['protocol_fee_rate'],v['fund_fee_rate']),(120000,40000))
  self.assertEqual(v['config_bytes_sha256'],hashlib.sha256(base64.b64decode(a['data'][0])).hexdigest())
 def test_config_bad_owner(self):
  _,_,a=f.pool_config();a['owner']=p.CLMM;self.assertRaisesRegex(RuntimeError,'OWNER',p.validate_cpmm_config,a)
 def test_config_bad_fee_sum(self):
  _,_,a=f.pool_config();raw=bytearray(base64.b64decode(a['data'][0]));f.put(raw,20,1000000);a['data'][0]=base64.b64encode(raw).decode()
  self.assertRaisesRegex(RuntimeError,'FEE_RANGE',p.validate_cpmm_config,a)
 def test_vault_wrong_mint(self):self.assertRaisesRegex(RuntimeError,'MINT',p.validate_vault,f.token_vault(p.WSOL),p.CANONICAL_MINTS['PENGU'])
 def test_vault_correct_amount(self):self.assertEqual(p.validate_vault(f.token_vault(p.WSOL,123),p.WSOL),123)

class EventDecode(unittest.TestCase):
 def decode(self,c):return r.decode_event(base64.b64decode(c['event_b64']),c['cfg'])
 def test_cpmm_all_sides_orientation_creator_and_exact_output(self):
  for sell in [True,False]:
   for m0 in [True,False]:
    for cr in [True,False]:
     for bi in [True,False]:
      c,_,_=f.cpmm(sell,m0,cr,bi);d=self.decode(c)
      for k,v in c['expected'].items():self.assertEqual(d[k],D(v) if k=='reference_post' else v)
      self.assertEqual(d['base_input'],bi)
 def test_cpmm_does_not_subtract_lp_fee_twice(self):
  c,_,_=f.cpmm();d=self.decode(c)
  self.assertEqual(d['post_meme_raw'],1000999100000)
  self.assertNotEqual(d['post_meme_raw'],1001000000000-2500000-500000)
 def test_clmm_prices_not_vault_ratio(self):
  for m0 in [True,False]:
   for sell in [True,False]:
    c,_,_=f.clmm(sell,m0);d=self.decode(c);v=r.tx_vaults(c['tx'],c['cfg'],d)
    self.assertEqual(d['reference_post'],D('0.001'));self.assertIsNone(d['reference_pre'])
    self.assertEqual(d['side'],'SELL' if sell else 'BUY')
    wrong=D(v['pre_quote_raw'])/D(v['pre_meme_raw'])/D(1000)
    self.assertNotEqual(wrong,d['reference_post'])
 def test_old_and_new_clmm_event_layout(self):
  for modern in [True,False]:
   c,_,_=f.clmm(new=modern);self.assertEqual(self.decode(c)['reference_post'],D('0.001'))
 def mutate(self,kind,offset,value,error,size=8):
  c,_,_=f.cpmm() if kind=='CPMM' else f.clmm();raw=bytearray(base64.b64decode(c['event_b64']));f.put(raw,offset,value,size)
  self.assertRaisesRegex(ValueError,error,r.decode_event,raw,c['cfg'])
 def test_cpmm_transferfee_rejected(self):self.mutate('CPMM',72,1,'TRANSFER_FEE')
 def test_clmm_transferfee_rejected(self):self.mutate('CLMM',144,1,'TRANSFER_FEE')
 def test_cpmm_bad_boolean(self):self.mutate('CPMM',169,2,'INVALID_BOOL',1)
 def test_clmm_bad_boolean(self):self.mutate('CLMM',168,2,'INVALID_BOOL',1)
 def test_cpmm_depleted_reserve(self):self.mutate('CPMM',64,2**64-1,'DEPLETED')
 def test_cpmm_overflow(self):self.mutate('CPMM',40,2**64-1,'OVERFLOW')
 def test_clmm_zero_sqrt(self):self.mutate('CLMM',169,0,'SQRT',16)
 def test_clmm_tick_mismatch(self):self.mutate('CLMM',201,100,'INCONSISTENT',4)
 def test_event_wrong_pool(self):
  c,_,_=f.cpmm();c['cfg']['pool']=f.key(99);self.assertRaisesRegex(ValueError,'IDENTITY',self.decode,c)
 def test_event_unknown_length(self):
  for fn in [f.cpmm,f.clmm]:
   c,_,_=fn();self.assertRaisesRegex(ValueError,'LAYOUT',r.decode_event,base64.b64decode(c['event_b64'])+b'x',c['cfg'])
 def test_bad_decimals(self):
  c,_,_=f.cpmm();c['cfg']['coin_decimals']=8;self.assertRaisesRegex(ValueError,'DECIMALS',self.decode,c)

class LogScopes(unittest.TestCase):
 def setUp(self):
  self.c,_,_=f.clmm();self.raw=base64.b64decode(self.c['event_b64']);self.cfg=self.c['cfg'];self.logs=f.logs(self.cfg,self.raw)
 def runlogs(self,logs):return r.scoped_swaps(logs,self.cfg['owner'],self.cfg['pool'])
 def test_direct_committed_event(self):self.assertEqual(self.runlogs(self.logs),[self.raw])
 def test_router_nested_committed_event(self):
  logs=['Program ROUTER invoke [1]',self.logs[0].replace('[1]','[2]'),*self.logs[1:],'Program ROUTER success']
  self.assertEqual(self.runlogs(logs),[self.raw])
 def test_inner_failed_event_discarded(self):
  logs=['Program ROUTER invoke [1]',self.logs[0].replace('[1]','[2]'),self.logs[1],'Program '+self.cfg['owner']+' failed: custom error','Program ROUTER success']
  self.assertEqual(self.runlogs(logs),[])
 def test_parent_failed_discards_child_success(self):
  logs=['Program ROUTER invoke [1]',self.logs[0].replace('[1]','[2]'),*self.logs[1:],'Program ROUTER failed: custom error']
  self.assertEqual(self.runlogs(logs),[])
 def test_program_log_spoof_ignored(self):
  self.assertEqual(self.runlogs(['Program FAKE invoke [1]',self.logs[1],'Program FAKE success']),[])
 def test_truncated_logs_fail_closed(self):self.assertRaisesRegex(ValueError,'TRUNCATED',self.runlogs,self.logs[:-1])
 def test_stack_mismatch_fail_closed(self):self.assertRaisesRegex(ValueError,'STACK',self.runlogs,[self.logs[0],'Program FAKE success'])
 def test_multiple_events_preserved_for_quarantine(self):self.assertEqual(len(self.runlogs(self.logs+self.logs)),2)
 def test_other_pool_ignored(self):
  raw=bytearray(self.raw);raw[8:40]=f.bkey(f.key(99));self.assertEqual(self.runlogs(f.logs(self.cfg,raw)),[])

class TxAndDownstream(unittest.TestCase):
 def setUp(self):self.c,_,_=f.clmm();self.d=r.decode_event(base64.b64decode(self.c['event_b64']),self.c['cfg'])
 def verify(self,tx):return r.tx_vaults(tx,self.c['cfg'],self.d,self.c['signature'],self.c['slot'])
 def test_exact_vault_deltas(self):self.assertEqual(self.verify(self.c['tx'])['pre_meme_raw'],8000000000000)
 def test_tx_failed(self):
  self.c['tx']['meta']['err']='bad';self.assertRaisesRegex(ValueError,'FAILED',self.verify,self.c['tx'])
 def test_tx_err_field_missing(self):
  del self.c['tx']['meta']['err'];self.assertRaisesRegex(ValueError,'MISSING',self.verify,self.c['tx'])
 def test_wrong_signature(self):
  self.c['tx']['transaction']['signatures']=[];self.assertRaisesRegex(ValueError,'SIGNATURE',self.verify,self.c['tx'])
 def test_wrong_slot(self):
  self.c['tx']['slot']=0;self.assertRaisesRegex(ValueError,'SLOT',self.verify,self.c['tx'])
 def test_vault_mint_mismatch(self):
  self.c['tx']['meta']['postTokenBalances'][0]['mint']='bad';self.assertRaisesRegex(ValueError,'IDENTITY',self.verify,self.c['tx'])
 def test_vault_delta_mismatch(self):
  self.c['tx']['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='1';self.assertRaisesRegex(ValueError,'DELTA',self.verify,self.c['tx'])
 def test_loaded_address_lookup_vaults(self):
  tx=self.c['tx'];keys=tx['transaction']['message']['accountKeys'];tx['meta']['loadedAddresses']['writable']=keys[3:]
  tx['transaction']['message']['accountKeys']=keys[:3];self.assertEqual(self.verify(tx)['pre_meme_raw'],8000000000000)
 def test_duplicate_balance_ambiguous(self):
  tx=self.c['tx'];tx['meta']['preTokenBalances'].append(copy.deepcopy(tx['meta']['preTokenBalances'][0]));self.assertRaisesRegex(ValueError,'AMBIGUOUS',self.verify,tx)
 def test_marks_and_runner_use_protocol_reference(self):
  for fn in [f.cpmm,f.clmm]:
   c,_,_=fn();row=f.row_from_case(c);m=Mark.from_row(row,1)
   self.assertEqual(m.post,D(c['expected']['reference_post']));self.assertAlmostEqual(run90m.reference(row),float(m.post))
 def test_modern_backfill_cannot_be_mark(self):
  row=f.row_from_case(self.c);row['source']='BACKFILL';self.assertRaisesRegex(ValueError,'BACKFILL',Mark.from_row,row,1)
 def test_tampered_raw_amount_rejected(self):
  row=f.row_from_case(self.c);row['meme_qty']*=2;self.assertRaisesRegex(ValueError,'AMOUNT',r.audit_row,row)
 def test_tampered_side_rejected(self):
  row=f.row_from_case(self.c);row['side']='BUY';self.assertRaisesRegex(ValueError,'SIDE',r.audit_row,row)
 def test_clmm_preprice_is_unknown_not_reserve_ratio(self):
  m=Mark.from_row(f.row_from_case(self.c),1);self.assertIsNone(m.pre);self.assertIn('SQRT_PRICE',m.meta(m.ts)['reference_kind'])
 def test_clmm_delta_evidence_tamper_rejected(self):
  row=f.row_from_case(self.c);row['protocol']['post_meme_raw']='1';self.assertRaisesRegex(ValueError,'DELTA',r.audit_row,row)

if __name__=='__main__':unittest.main()

class IntegratedMultiProtocolPreflight(unittest.TestCase):
 def setup_fixture(self):
  from test_preflight_fix1 import fixture,response,mint_account
  targets,accounts,oldhttp=fixture();modern_meta={}
  for idx,(sym,addr,mint) in enumerate(targets[6:]):
   kind='CLMM' if sym=='PUMP' else 'CPMM';cfg,acc,conf=f.pool_config(kind,meme0=(idx%2==0))
   raw=bytearray(base64.b64decode(acc['data'][0]));m0=idx%2==0
   at=(168 if m0 else 200) if kind=='CPMM' else (73 if m0 else 105)
   raw[at:at+32]=p.pubkey_bytes(mint)
   v0,v1,config=f.key(40+2*idx),f.key(41+2*idx),f.key(70+idx)
   offs=(72,104,8) if kind=='CPMM' else (137,169,9)
   for at,value in zip(offs,[v0,v1,config]):raw[at:at+32]=p.pubkey_bytes(value)
   acc['data'][0]=base64.b64encode(raw).decode();accounts[addr]=acc;accounts[config]=conf
   accounts[v0]=f.token_vault(mint if m0 else p.WSOL);accounts[v1]=f.token_vault(p.WSOL if m0 else mint)
   accounts[mint]=mint_account(6)
   modern_meta[mint]={'id':addr,'programId':p.CPMM if kind=='CPMM' else p.CLMM,'type':kind,'tvl':100000,
       'mintA':{'address':mint},'mintB':{'address':p.WSOL}}
  def http(url,body=None,**kwargs):
   if body is not None:return oldhttp(url,body,**kwargs)
   from urllib.parse import urlparse,parse_qs
   q=parse_qs(urlparse(url).query);m=next(x for x in [q['mint1'][0],q['mint2'][0]] if x!=p.WSOL)
   return response([modern_meta[m]])
  return targets,accounts,http
 def test_twelve_pool_state_mints_vaults_and_fee_config(self):
  targets,accounts,http=self.setup_fixture()
  with tempfile.TemporaryDirectory() as td,patch.object(p,'http',side_effect=http) as mock:
   got=p.check('fixture',Path(td))
   self.assertTrue(got['ok']);self.assertEqual(len(got['pools']),12)
   self.assertEqual([g['pool_kind'] for g in got['pools']].count('CPMM'),5)
   self.assertEqual([g['pool_kind'] for g in got['pools']].count('CLMM'),1)
   self.assertEqual(mock.call_count,9)
   for g in got['pools']:
    self.assertEqual(g['pool_bytes_sha256'],hashlib.sha256(base64.b64decode(g['pool_data_b64'])).hexdigest())
    if g['pool_kind']=='CPMM':self.assertEqual(g['protocol_fee_rate'],120000)
 def test_new_pool_wrong_vault_identity_blocks(self):
  _,accounts,http=self.setup_fixture();accounts[f.key(40)]=f.token_vault(p.WSOL)
  with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'VAULT_LAYOUT_MINT',p.check,'fixture')
 def test_fee_config_wrong_owner_blocks(self):
  _,accounts,http=self.setup_fixture();accounts[f.key(70)]['owner']=p.CLMM
  with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'CPMM_CONFIG_OWNER',p.check,'fixture')
 def test_metadata_rpc_program_mismatch_blocks(self):
  targets,accounts,http=self.setup_fixture();sym,addr,mint=targets[6]
  # Correct layout for CLMM returned by RPC, but discovery claimed CPMM.
  cfg,acc,_=f.pool_config('CLMM');raw=bytearray(base64.b64decode(acc['data'][0]));raw[105:137]=p.pubkey_bytes(mint)
  accounts[cfg['coin_vault']]=f.token_vault(p.WSOL);accounts[cfg['pc_vault']]=f.token_vault(mint)
  acc['data'][0]=base64.b64encode(raw).decode();accounts[addr]=acc
  with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'PROGRAM_MISMATCH',p.check,'fixture')
