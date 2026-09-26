import unittest

from a51.run_manager import complete_prefix, primary_ready, self_test


class A51RRecoveryTests(unittest.TestCase):
    def test_self_test(self):
        self.assertTrue(self_test())

    def test_complete_prefix_drops_only_partial_tail(self):
        prefix, dropped = complete_prefix(b'{"a":1}\n{"b":2}')
        self.assertEqual(prefix, b'{"a":1}\n')
        self.assertEqual(dropped, len(b'{"b":2}'))

    def test_complete_prefix_keeps_complete_file(self):
        data = b'{"a":1}\n'
        prefix, dropped = complete_prefix(data)
        self.assertEqual(prefix, data)
        self.assertEqual(dropped, 0)

    def test_primary_ready_requires_integrity(self):
        platform = {
            "integrity": {"ok": False},
            "coverage": {
                "live_signal_sources": 1,
                "usable_books": 1,
                "paper_prerequisites_ready": 1,
            },
        }
        self.assertFalse(primary_ready(platform))

    def test_primary_ready_requires_all_three_market_gates(self):
        for missing in (
            "live_signal_sources",
            "usable_books",
            "paper_prerequisites_ready",
        ):
            coverage = {
                "live_signal_sources": 1,
                "usable_books": 1,
                "paper_prerequisites_ready": 1,
            }
            coverage[missing] = 0
            self.assertFalse(primary_ready({
                "integrity": {"ok": True},
                "coverage": coverage,
            }))


if __name__ == "__main__":
    unittest.main()
