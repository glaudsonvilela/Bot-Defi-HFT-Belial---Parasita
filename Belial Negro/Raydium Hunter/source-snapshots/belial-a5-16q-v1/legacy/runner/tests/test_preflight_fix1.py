"""Offline regressions. Mocked accounts/metadata are NOT a live market preflight."""
import base64
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'runner/tools'))
import preflight_pools as p


def pool_id(n):
    return p.b58(hashlib.sha256(('offline-fixture-pool-'+str(n)).encode()).digest())


def metadata(mint, n=0, owner=None, tvl=100):
    return {'id':pool_id(n),'tvl':tvl,'programId':owner or p.AMM,
            'mintA':{'address':mint},'mintB':{'address':p.WSOL}}


def response(rows, **kwargs):
    return {'success':True,'data':{'data':rows,**kwargs}}


def pool_account(mint, decimals=6, reverse=False):
    raw=bytearray(752)
    raw[32:40]=(9 if reverse else decimals).to_bytes(8,'little')
    raw[40:48]=(decimals if reverse else 9).to_bytes(8,'little')
    raw[400:432]=p.pubkey_bytes(p.WSOL if reverse else mint)
    raw[432:464]=p.pubkey_bytes(mint if reverse else p.WSOL)
    return {'owner':p.AMM,'executable':False,'data':[base64.b64encode(raw).decode(),'base64']}


def mint_account(decimals=6):
    raw=bytearray(82);raw[44]=decimals;raw[45]=1
    return {'owner':p.TOKEN,'executable':False,'data':[base64.b64encode(raw).decode(),'base64']}


def fixture():
    targets=list(p.ORIGINAL)+[(s,pool_id(i),m) for i,(s,m) in enumerate(p.canonical_mints().items(),20)]
    accounts={key:pool_account(m,reverse=(i%2==1)) for i,(_,key,m) in enumerate(targets)}
    accounts.update({m:mint_account() for _,_,m in targets});accounts[p.WSOL]=mint_account(9)
    def http(url,body=None,**kwargs):
        if body is not None:
            assert body['method']=='getMultipleAccounts',body
            return {'result':{'context':{'slot':100000},'value':[accounts.get(k) for k in body['params'][0]]}}
        assert '/mint/list' not in url,'Legacy ticker registry must never be requested'
        query=parse_qs(urlparse(url).query)
        keys={query['mint1'][0],query['mint2'][0]};keys.remove(p.WSOL);mint=keys.pop()
        target=next(t for t in targets if t[2]==mint)
        row=metadata(mint);row['id']=target[1]
        return response([row])
    return targets,accounts,http


class CanonicalIdentityFIX1(unittest.TestCase):
    def test_all_twelve_token_keys_are_distinct_canonical_pubkeys(self):
        keys=[m for _,_,m in p.ORIGINAL]+list(p.canonical_mints().values())
        self.assertEqual(len(set(keys)),12)
        for key in keys:self.assertEqual(len(p.pubkey_bytes(key)),32)
    def test_manifest_tamper_is_rejected(self):
        with patch.dict(p.CANONICAL_MINTS,{'BONK':p.CANONICAL_MINTS['PYTH']}):
            self.assertRaisesRegex(RuntimeError,'MANIFEST_MISMATCH',p.canonical_mints)
    def test_mint_result_does_not_alias_frozen_dictionary(self):
        result=p.canonical_mints();result['BONK']='bad'
        self.assertNotEqual(p.canonical_mints()['BONK'],'bad')
    def test_base58_invalid_alphabet_is_rejected(self):
        self.assertRaisesRegex(RuntimeError,'ALPHABET',p.pubkey_bytes,'0'*32)
    def test_base58_oversized_value_is_rejected(self):
        self.assertRaises(RuntimeError,p.pubkey_bytes,'z'*44)
    def test_no_live_registry_request_in_full_success_path(self):
        targets,accounts,http=fixture()
        with patch.object(p,'http',side_effect=http) as mock:
            report=p.check('https://fixture.invalid')
        self.assertTrue(report['ok']);self.assertEqual(len(report['pools']),12)
        self.assertEqual([r['symbol'] for r in report['pools']],list(p.SYMBOLS))
        self.assertEqual(mock.call_count,8) # Six discovery queries + two RPC batches.
        self.assertNotIn('registry_sha256',report)
        self.assertEqual(report['identity_source'],'PINNED_MAINNET_MINTS')
    def test_old_missing_bonk_failure_is_reproduced_but_new_identity_survives(self):
        registry={'success':True,'data':{'mintList':[]}}
        def old_gate(value):
            xs=value['data']['mintList']
            matches={x['address'] for x in xs if x.get('chainId')==101 and x.get('symbol')=='BONK'}
            if len(matches)!=1:raise RuntimeError(f'BONK:CANONICAL_MINT_MISSING_OR_AMBIGUOUS:{len(matches)}')
        self.assertRaisesRegex(RuntimeError,'BONK:CANONICAL_MINT_MISSING_OR_AMBIGUOUS:0',old_gate,registry)
        self.assertEqual(p.canonical_mints()['BONK'],'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263')
    def test_spoof_symbol_does_not_override_exact_mint(self):
        wrong=metadata(p.CANONICAL_MINTS['PYTH']);wrong['mintA']['symbol']='BONK'
        self.assertRaisesRegex(RuntimeError,'NO_COMPATIBLE',p.choose_legacy,response([wrong]),p.CANONICAL_MINTS['BONK'])
    def test_source_urls_present_for_every_pinned_mint(self):
        doc=json.loads((ROOT/'runner/CANONICAL_MINTS.json').read_text())
        for sym in p.SYMBOLS[6:]:
            self.assertTrue(doc['tokens'][sym]['sources'])
            self.assertEqual(doc['tokens'][sym]['mint'],p.CANONICAL_MINTS[sym])


class PoolDiscoveryFIX1(unittest.TestCase):
    def setUp(self):self.mint=p.CANONICAL_MINTS['BONK']
    def test_sdk_v2_uses_sorted_mints_without_excluding_clmm(self):
        url=p.discovery_url(self.mint,'list-v2');q=parse_qs(urlparse(url).query)
        self.assertIn('/pools/info/list-v2?',url)
        self.assertNotIn('poolType',q)
        self.assertEqual(q['sortField'],['liquidity'])
        self.assertEqual([q['mint1'][0],q['mint2'][0]],sorted([self.mint,p.WSOL]))
    def test_legacy_metadata_fallback_after_endpoint_error(self):
        with patch.object(p,'http',side_effect=[RuntimeError('HTTP_404'),response([metadata(self.mint)])]):
            row,info=p.resolve_pool('BONK',self.mint)
        self.assertEqual(row['id'],pool_id(0));self.assertEqual(info['attempts'][0]['error'],'HTTP_404')
        self.assertEqual(info['attempts'][1]['style'],'legacy')
    def test_cursor_pagination_discovers_valid_legacy_pool(self):
        values=[response([metadata(self.mint,owner='CPMM')],nextPageId='cursor-one'),response([metadata(self.mint)])]
        with patch.object(p,'http',side_effect=values) as mock:
            row,info=p.resolve_pool('BONK',self.mint)
        self.assertIn('nextPageId=cursor-one',mock.call_args_list[1].args[0]);self.assertEqual(len(info['attempts']),2)
    def test_cursor_repetition_does_not_loop(self):
        values=[response([],nextPageId='x'),response([],nextPageId='x'),response([metadata(self.mint)])]
        with patch.object(p,'http',side_effect=values) as mock:row,info=p.resolve_pool('BONK',self.mint)
        self.assertEqual(mock.call_count,3);self.assertIn('CURSOR_REPEATED',info['attempts'][1]['error'])
    def test_missing_cursor_has_next_page_uses_old_pagination(self):
        values=[response([],hasNextPage=True),response([],hasNextPage=True),response([metadata(self.mint)])]
        with patch.object(p,'http',side_effect=values) as mock:row,info=p.resolve_pool('BONK',self.mint)
        self.assertIn('page=2',mock.call_args_list[2].args[0]);self.assertEqual(info['attempts'][2]['page'],2)
    def test_unknown_program_id_never_passes_supported_decoders(self):
        with patch.object(p,'http',return_value=response([metadata(self.mint,owner='CPMM')])):
            self.assertRaisesRegex(RuntimeError,'NO_COMPATIBLE_SUPPORTED',p.resolve_pool,'BONK',self.mint)
    def test_bad_pair_never_passes(self):
        row=metadata(self.mint);row['mintB']['address']=p.CANONICAL_MINTS['PYTH']
        self.assertRaisesRegex(RuntimeError,'NO_COMPATIBLE',p.choose_legacy,response([row]),self.mint)
    def test_bad_liquidity_rejected(self):
        for value in [float('nan'),float('inf'),-1,'invalid']:
            self.assertRaisesRegex(RuntimeError,'NO_COMPATIBLE',p.choose_legacy,response([metadata(self.mint,tvl=value)]),self.mint)
    def test_malformed_schema_is_not_reported_as_absence(self):
        with patch.object(p,'http',return_value={'success':True,'data':{'wrong':[]}}):
            self.assertRaisesRegex(RuntimeError,'INCOMPLETE_OR_API_ERROR',p.resolve_pool,'BONK',self.mint)
    def test_network_error_is_not_reported_as_absence(self):
        with patch.object(p,'http',side_effect=RuntimeError('METADATA_HTTP_FAILED:DNS')):
            self.assertRaisesRegex(RuntimeError,'INCOMPLETE_OR_API_ERROR',p.resolve_pool,'BONK',self.mint)
    def test_failure_artifacts_retain_both_endpoint_attempts(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(p,'http',return_value=response([])):
            self.assertRaises(RuntimeError,p.resolve_pool,'BONK',self.mint,Path(temp))
            doc=json.loads((Path(temp)/'BONK_FAILED.json').read_text())
            self.assertEqual(len(doc['attempts']),2);self.assertEqual(doc['mint'],self.mint)
            self.assertFalse(doc['scan_started'])
    def test_tie_break_is_deterministic(self):
        rows=[metadata(self.mint,i) for i in [3,1,2]]
        one=p.choose_legacy(response(rows),self.mint)['id']
        self.assertEqual(p.choose_legacy(response(rows[::-1]),self.mint)['id'],one)


class RPCAndSessionFIX1(unittest.TestCase):
    def test_onchain_wrong_pool_owner_blocks_scan(self):
        targets,accounts,http=fixture();accounts[targets[6][1]]['owner']='CPMM'
        with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'BONK:OWNER',p.check,'fixture')
    def test_onchain_mint_decimals_must_match_pool(self):
        targets,accounts,http=fixture();accounts[targets[6][2]]=mint_account(7)
        with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'MINT_DECIMALS',p.check,'fixture')
    def test_onchain_wrong_mint_owner_blocks_scan(self):
        targets,accounts,http=fixture();accounts[targets[6][2]]['owner']='Token2022Unsupported'
        with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'MINT_OWNER',p.check,'fixture')
    def test_missing_mint_blocks_scan(self):
        targets,accounts,http=fixture();accounts[targets[6][2]]=None
        with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'MINT_OWNER',p.check,'fixture')
    def test_uninitialized_mint_blocks_scan(self):
        targets,accounts,http=fixture();value=mint_account();raw=bytearray(base64.b64decode(value['data'][0]));raw[45]=0
        value['data'][0]=base64.b64encode(raw).decode();accounts[targets[6][2]]=value
        with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'INITIALIZATION',p.check,'fixture')
    def test_bad_wsol_decimals_blocks_scan(self):
        targets,accounts,http=fixture();accounts[p.WSOL]=mint_account(8)
        with patch.object(p,'http',side_effect=http):self.assertRaisesRegex(RuntimeError,'MINT_DECIMALS',p.check,'fixture')
    def test_rpc_length_mismatch_is_not_silently_zipped(self):
        with patch.object(p,'http',return_value={'result':{'value':[],'context':{'slot':1}}}):
            self.assertRaisesRegex(RuntimeError,'COUNT_MISMATCH',p.rpc,'fixture',['some-key'])
    def test_rpc_error_is_not_empty_success(self):
        with patch.object(p,'http',return_value={'error':{'code':429}}):self.assertRaisesRegex(RuntimeError,'RPC',p.rpc,'fixture',['x'])
    def test_all_six_discovery_failures_are_reported(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(p,'resolve_pool',side_effect=RuntimeError('NO_POOL')) as mock,patch.object(p,'rpc') as rpc:
            self.assertRaisesRegex(RuntimeError,'DISCOVERY_BLOCKED',p.check,'fixture',Path(temp))
            self.assertEqual(mock.call_count,6);rpc.assert_not_called()
            doc=json.loads((Path(temp)/'RESOLUTION_SUMMARY.json').read_text())
            self.assertEqual([f['symbol'] for f in doc['failures']],list(p.SYMBOLS[6:]))
            self.assertFalse(doc['scan_started'])
    def test_duplicate_pool_addresses_are_rejected(self):
        with patch.object(p,'resolve_pool',return_value=({'id':p.ORIGINAL[0][1]},{})),patch.object(p,'rpc') as rpc:
            self.assertRaisesRegex(RuntimeError,'DUPLICATE_POOL',p.check,'fixture');rpc.assert_not_called()
    def test_success_report_still_never_starts_capture(self):
        targets,accounts,http=fixture()
        with tempfile.TemporaryDirectory() as temp,patch.object(p,'http',side_effect=http):
            report=p.check('fixture',Path(temp));doc=json.loads((Path(temp)/'RESOLUTION_SUMMARY.json').read_text())
            self.assertEqual(doc['onchain_verified_pools'],12);self.assertFalse(doc['scan_started'])
            self.assertTrue(report['paper_only'])
    def test_runner_release_is_isolated_and_ninety_minutes_selected(self):
        from session_config import CAPTURE_MINUTES,CAPTURE_SECONDS
        self.assertEqual((CAPTURE_MINUTES,CAPTURE_SECONDS),(90,5400))
        for name in ('install_and_run.py','run90m.py'):
            self.assertIn('r5d-t3-90m-mp12-ultra-v1-fix4',(ROOT/'runner/tools'/name).read_text())


if __name__=='__main__':unittest.main()
