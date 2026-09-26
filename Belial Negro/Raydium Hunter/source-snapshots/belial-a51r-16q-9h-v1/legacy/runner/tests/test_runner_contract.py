import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
RUN=(ROOT/'runner/tools/run90m.py').read_text()
INSTALL=(ROOT/'runner/tools/install_and_run.py').read_text()
D2=(ROOT/'d2/tools/dashboard_ultra.py').read_text()

class T3RunnerContract(unittest.TestCase):
    def test_exact_90m_and_no_transactions(self):
        self.assertIn("if a.minutes!=CAPTURE_MINUTES", RUN)
        self.assertIn("'--minutes',str(CAPTURE_MINUTES)", RUN)
        self.assertIn("'TRANSACTIONS_SUBMITTED':False", RUN)
        self.assertIn("'SIGNAL_ENGINE_FEEDBACK':False", RUN)
        self.assertIn("'MAIN_STRATEGY_MODIFIED':False", RUN)
    def test_main_tail_old_pools_only_shadow_all_six(self):
        self.assertIn("OLD_POOLS={'FARTCOIN','BOME','WIF'}", RUN)
        self.assertIn("if sym in OLD_POOLS:mt.feed", RUN)
        self.assertIn("st.feed(kind,row", RUN)
        self.assertIn("'main_strategy_pnl':'FARTCOIN_ONLY_UNCHANGED'", RUN)
        self.assertIn("'shadow_compare_pnl':'COUNTERFACTUAL_NOT_STRATEGY'", RUN)
    def test_dashboard_event_driven_contract(self):
        self.assertIn('createfilehandler',D2)
        self.assertIn('fast_event_render(changed)',D2)
        self.assertIn("self.root.after(8,self.render)",D2)
        self.assertIn('microseconds_are_measurement_precision_not_screen_refresh_rate',D2)
    def test_installer_offline_rust_preflight_and_rollback(self):
        self.assertIn("'--offline'",INSTALL)
        self.assertIn('preflight_pools.py',INSTALL)
        self.assertIn('ROLLBACK_T3_MP12.sh',INSTALL)
        self.assertIn('PAYLOAD_MANIFEST_MISMATCH',INSTALL)
        self.assertIn("OPENCL",INSTALL.upper())

if __name__=='__main__':unittest.main()
