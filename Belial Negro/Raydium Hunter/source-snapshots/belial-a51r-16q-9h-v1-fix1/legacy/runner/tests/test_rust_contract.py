import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / 'mp12/engine/src/main.rs').read_text(encoding='utf-8')

class RustMP12Contract(unittest.TestCase):
    def test_twelve_pool_contract_and_frozen_original_addresses(self):
        self.assertIn('const POOL_SYMBOLS: [&str; 12] = ["FARTCOIN", "BOME", "WIF", "POPCAT", "RAY", "JUP", "BONK", "PENGU", "PUMP", "USELESS", "GRASS", "PYTH"];', SRC)
        expected = {
            'Bzc9NZfMqkXR6fz1DBph7BDf9BroyEf6pnzESP7v5iiw',
            'DSUvc5qf5LJHHV5e2tD184ixotSnCnwj7i4jJa4Xsrmt',
            'EP2ib6dYdEeqD8MfE2ezHCxX3kP3K2eLKkirfPm5eyMx',
            'FRhB8L7Y9Qq41qZXYLtC2nw8An1RJfLLxRF2x9RwLLMo',
            'AVs9TA4nWDzfPJE9gGVNJMVhcQy3V9PGazuz33BfG2RA',
            'EYErUp5muPYEEkeaUCY22JibeZX7E9UuMcJFZkmNAN7c',
        }
        for address in expected:
            self.assertIn(address, SRC)
        self.assertEqual(SRC.count('PoolCfg {'), 8)  # declaration + old-six fixture + extension factory
        self.assertNotIn('["FARTCOIN", "BOME", "WIF"]', SRC)

    def test_nanosecond_pipeline_contract(self):
        for marker in (
            'received_unix_ns',
            'writer_observed_unix_ns',
            'collector_to_writer_us',
            'collector_decode_us',
            'UNIX_NS_LOCAL_RECEIVE_NOT_BLOCKCHAIN_TIMESTAMP',
            'MP12_ULTRA_V1',
        ):
            self.assertIn(marker, SRC)
        self.assertRegex(SRC, r'let\s+received_ns\s*=\s*now_ns\(\)')
        self.assertIn('(writer_ns - received_ns) as f64 / 1_000.0', SRC)

    def test_hot_loops_use_pool_symbols(self):
        # The collector fan-out, horizon updater and final label flush must all use the frozen six-pool list.
        self.assertGreaterEqual(len(re.findall(r'for symbol in POOL_SYMBOLS', SRC)), 3)
        self.assertIn('let pool_metrics = POOL_SYMBOLS', SRC)

if __name__ == '__main__':
    unittest.main()
