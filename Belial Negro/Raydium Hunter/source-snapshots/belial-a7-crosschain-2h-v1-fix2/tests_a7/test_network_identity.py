"""Independent SDK fixtures catch the original production constant typo."""
import ast,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from a7.network_identity import check_solana_mainnet,MAINNET_GENESIS
from a7.selection import b58
from a51.common import ROOT
from a7.ui import final_html
class NetworkIdentityTests(unittest.TestCase):
 def check(self,result,ok):
  with tempfile.TemporaryDirectory() as d:
   rpc=Mock();rpc.call.return_value=result;p=Path(d)
   if ok:check_solana_mainnet(rpc,p)
   else:
    with self.assertRaisesRegex(RuntimeError,'SOLANA_MAINNET_GENESIS_MISMATCH'):check_solana_mainnet(rpc,p)
   rpc.call.assert_called_once_with('getGenesisHash')
   proof=json.loads((p/'NETWORK_CHECK_SOLANA.json').read_text());self.assertEqual(proof['passed'],ok);self.assertFalse(proof['orders'])
 def test_official_mainnet_response_accepted(self):
  self.check('5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d',True)
 def test_devnet_and_testnet_refused(self):
  for value in ('EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG','4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY'):
   with self.subTest(value=value):self.check(value,False)
 def test_original_typo_refused(self):self.check('5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2x',False)
 def test_invalid_response_refused_and_recorded(self):self.check(None,False)
 def test_hash_is_32_bytes_and_matches_previous_validated_collector(self):
  self.assertEqual(len(b58(MAINNET_GENESIS)),32)
  tree=ast.parse((ROOT/'legacy/runner/tools/rpc_transport.py').read_text())
  previous=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MAINNET_GENESIS' for t in n.targets))
  self.assertEqual(MAINNET_GENESIS,previous)
 def test_failure_report_includes_diagnostic_without_live_listener(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);final_html(p,dict(phase='FAILED_OR_INTERRUPTED',failures={'runtime':'SOLANA_MAINNET_GENESIS_MISMATCH'},orders=False))
   text=(p/'RELATORIO.html').read_text();self.assertIn('SOLANA_MAINNET_GENESIS_MISMATCH',text);self.assertIn('Object.entries(s.failures)',text);self.assertNotIn('await fetch',text)
if __name__=='__main__':unittest.main()
