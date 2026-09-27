import json
import unittest
from a5.common import ROOT,CORE12,FOCUS4,SYMBOLS,DURATION_MS,cohort_of

EXPECTED=('FARTCOIN','BOME','WIF','POPCAT','RAY','JUP','BONK','PENGU','PUMP','USELESS','GRASS','PYTH','RENDER','KMNO','MEW','MPLX')
REMOVED={'JTO','W','TNSR','DRIFT','TRUMP','MELANIA','ORCA','HNT'}

class A516QContractTests(unittest.TestCase):
    def test_exact_16q_order(self):
        self.assertEqual(SYMBOLS,EXPECTED)
        self.assertEqual(len(SYMBOLS),16)
        self.assertEqual(len(set(SYMBOLS)),16)
    def test_groups(self):
        self.assertEqual(len(CORE12),12)
        self.assertEqual(FOCUS4,('RENDER','KMNO','MEW','MPLX'))
        self.assertTrue(all(cohort_of(s)=='CORE12' for s in CORE12))
        self.assertTrue(all(cohort_of(s)=='FOCUS4' for s in FOCUS4))
    def test_removed_assets_not_in_a5_universe(self):
        self.assertFalse(REMOVED.intersection(SYMBOLS))
    def test_universe_file(self):
        doc=json.loads((ROOT/'UNIVERSE_A5_16Q.json').read_text())
        self.assertEqual(doc['requested_assets'],16)
        self.assertEqual(tuple(doc['core12']),CORE12)
        self.assertEqual(tuple(x['symbol'] for x in doc['focus4']),FOCUS4)
        self.assertFalse(doc['orders'])
    def test_duration_and_orders_contract(self):
        self.assertEqual(DURATION_MS,5_400_000)
        self.assertFalse(json.loads((ROOT/'UNIVERSE_A5_16Q.json').read_text())['orders'])
    def test_native_scope_is_16(self):
        src=(ROOT/'engine_a5/src/main.rs').read_text()
        self.assertIn('const POOL_SYMBOLS: [&str;16]',src)
        self.assertIn('const A1_MINTS: [&str;16]',src)
        self.assertIn('else {16}',src)
        self.assertNotIn('const POOL_SYMBOLS: [&str;24]',src)
    def test_raw_and_ws_caps_are_16(self):
        raw=(ROOT/'engine_a5/src/bin/belial-spot-raw.rs').read_text()
        ws=(ROOT/'a5/ws_relay.py').read_text()
        self.assertIn('rows.len()>16',raw)
        self.assertIn('RAW_MAX_SIXTEEN',raw)
        self.assertNotIn('RAW_MAX_TWENTY_FOUR',raw)
        self.assertIn('len(self.pools)>16',ws)
        self.assertIn('len(client.subs)>=16',ws)
    def test_dashboard_is_16q(self):
        html=(ROOT/'a5/web/dashboard.html').read_text()
        self.assertIn("const EXP=['RENDER','KMNO','MEW','MPLX'];",html)
        self.assertIn('value="FOCUS4"',html)
        self.assertNotIn('value="EXPANSION12"',html)
        self.assertIn('16 candidatos',html)
    def test_supervisor_refuses_parallel_a4_or_a5(self):
        src=(ROOT/'a5/supervisor.py').read_text()
        self.assertIn("b'a4.supervisor' in raw",src)
        self.assertIn("b'a5.supervisor' in raw",src)
    def test_integrity_reason_drift_is_not_asset_drift(self):
        src=(ROOT/'a5/supervisor.py').read_text()
        self.assertIn('WALL_CLOCK_DRIFT',src)
        self.assertNotIn("'DRIFT'",src)
        self.assertNotIn('"DRIFT"',src)
    def test_a5_test_fixture_is_universe_clean(self):
        terminal=(ROOT/'tests_a5/test_terminal.py').read_text()
        paper=(ROOT/'tests_a5/paper_harness_a5.py').read_text()
        fixture=(ROOT/'tests_a5/fixture_venue_a5.py').read_text()
        self.assertIn('class A5Harness:',terminal)
        self.assertIn("'RENDER'",terminal)
        self.assertNotIn('old.Harness(',terminal)
        self.assertIn("tests_a5/fixture_venue_a5.py",paper)
        self.assertIn('Stdlib-only',fixture)
        self.assertNotIn('from a5.',fixture)
        self.assertNotIn('test_books_paper',fixture)
    def test_a4_remains_present_and_separate(self):
        self.assertTrue((ROOT/'a4/common.py').is_file())
        self.assertTrue((ROOT/'engine_a4/src/main.rs').is_file())
        self.assertNotEqual((ROOT/'a4/common.py').read_text(),(ROOT/'a5/common.py').read_text())
