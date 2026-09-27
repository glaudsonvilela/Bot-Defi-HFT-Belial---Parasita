"""Static release/ABI contracts are NOT Rust compilation or on-chain evidence."""
import ast,copy,hashlib,json,re,tempfile,unittest
from pathlib import Path
from a2.common import ROOT,CORE12,EXPANSION12,SYMBOLS,DURATION_MS,command
from a2.economics import validate_costs
from a2.rpc_relay import Budget,SharedReads

class ReleaseContracts(unittest.TestCase):
 def test_duration_preserved_90m(self):self.assertEqual(DURATION_MS,5400000)
 def test_new_names_all_commands(self):self.assertIn('a2.paper_worker',command('paper_worker'))
 def test_native_uses_new_manifest_not_legacy(self):self.assertIn("ROOT/'engine_a2/Cargo.toml'",(ROOT/'a2/build_worker.py').read_text())
 def test_no_full_compile_claim(self):self.assertIn('native_rust_executed', (ROOT/'a2/verify.py').read_text())
 def test_new_bin_matches_pinned_mints(self):
  from a2.spot_validation import pf
  mint_by_sym=dict(pf.canonical_mints());mint_by_sym.update({x[0]:x[2] for x in pf.ORIGINAL})
  mint_by_sym.update({x['symbol']:x['mint'] for x in json.loads((ROOT/'UNIVERSE_A2.json').read_text())['expansion12']})
  text=(ROOT/'engine_a2/src/main.rs').read_text();part=text.split('const A1_MINTS:')[1].split('];',1)[0]
  self.assertEqual(re.findall(r'"([A-Za-z0-9]+)"',part),[mint_by_sym[s] for s in SYMBOLS])
 def test_readonly_models_unchanged(self):
  for name in ('m2_core.rs','raydium_protocols.rs','token_programs.rs'):
   self.assertEqual((ROOT/'engine/src'/name).read_bytes(),(ROOT/'engine_a2/src'/name).read_bytes())
 def test_cargo_lock_dependencies_preserved(self):
  for name in ('Cargo.lock','Cargo.toml'):self.assertEqual((ROOT/'engine'/name).read_bytes(),(ROOT/'engine_a2'/name).read_bytes())
 def test_native_actor_warmup_emitted(self):self.assertIn('value["a2_actor_not_before_ms"]',(ROOT/'engine_a2/src/main.rs').read_text())
 def test_label_only_sampling_no_tail_import(self):
  code=(ROOT/'a2/price_samples.py').read_text();self.assertNotIn('from paper',code);self.assertNotIn('import tail',code);self.assertIn("'not_a_swap':True",code)
 def test_profile_unknown_is_not_zero(self):
  cfg=validate_costs(json.loads((ROOT/'CONFIG_CUSTOS_EXTERNOS.json').read_text()))
  for venue in ('hyperliquid','lighter'):self.assertTrue(all(x is None for x in cfg[venue].values()))
 def test_heads_same(self):
  old=json.loads((ROOT/'legacy/n2_mp12/CONFIG_N2.json').read_text());new=json.loads((ROOT/'n2_a2/CONFIG_N2.json').read_text())
  self.assertEqual(old['heads'],new['heads']);self.assertEqual(len(new['heads']),32)
 def test_no_time_reset_for_price_sampler(self):
  self.assertNotIn("phase='CAPTURING'",(ROOT/'a2/price_samples.py').read_text())
 def test_capture_counts_literal_fixed(self):
  s=(ROOT/'a2/supervisor.py').read_text();self.assertIn('REQUESTED=24;',s);self.assertNotIn('REQUESTED=12;',s)
 def test_no_old_weight_reinterpretation(self):self.assertIn('A2_NEW_LABEL_REVISION_FRESH_MODEL_OLD_WEIGHTS_PRESERVED',(ROOT/'a2/n2_worker.py').read_text())
 def test_external_costs_frozen_before_capture(self):self.assertIn("'COSTS_FROZEN.json'",(ROOT/'a2/supervisor.py').read_text())
 def test_funding_tail_no_postdeadline_books(self):
  text=(ROOT/'a2/paper_worker.py').read_text().split('grace=time.monotonic()+60')[1].split('except BaseException')[0]
  self.assertNotIn("process('book'",text);self.assertNotIn("process('signal'",text);self.assertIn("process('funding'",text)
 def test_universe_provenance_per_mint(self):
  r=json.loads((ROOT/'UNIVERSE_A2.json').read_text())
  for row in r['expansion12']:self.assertTrue(row.get('source') or row.get('sources'))

class ProfileMeaning(unittest.TestCase):
 def test_unknown_profile_cannot_be_mislabeled_as_complete(self):
  value=json.loads((ROOT/'CONFIG_CUSTOS_EXTERNOS.json').read_text());value['lighter']['network_entry_usd']=0
  self.assertRaises(ValueError,validate_costs,value)
