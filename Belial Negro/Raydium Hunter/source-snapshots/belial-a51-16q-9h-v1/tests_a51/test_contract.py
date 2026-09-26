import ast
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from a51.cache import JsonCache
from a51.common import ROOT, SYMBOLS, CORE12, FOCUS4, MINUTES, DURATION_MS, VERSION
from a51.satellite_worker import self_test, aggregate_symbol
from a51.ui_worker import Bus, make_server


class A51ContractTests(unittest.TestCase):
    def test_exact_16q_universe_preserved(self):
        self.assertEqual(
            (len(SYMBOLS), len(CORE12), FOCUS4),
            (16, 12, ("RENDER", "KMNO", "MEW", "MPLX")),
        )

    def test_nine_hour_duration(self):
        self.assertEqual(
            (MINUTES, DURATION_MS, VERSION),
            (540, 32_400_000, "BELIAL_A51_16Q_9H_V1"),
        )

    def test_satellite_self_test(self):
        self.assertTrue(self_test())

    def test_satellites_are_not_in_paper_decision_modules(self):
        for relative in (
            "a51/paper_controller.py",
            "a51/analysis_worker.py",
            "a51/economics.py",
        ):
            source = (ROOT / relative).read_text()
            self.assertNotIn("satellite_worker", source)
            self.assertNotIn("satellites/state.json", source)

    def test_satellite_worker_is_read_only_shadow(self):
        source = (ROOT / "a51/satellite_worker.py").read_text()
        tree = ast.parse(source)

        # Runtime semantic contract; independent of formatter/quote style.
        probe = aggregate_symbol("WIF", {}, 0)
        self.assertIs(probe["affects_trading"], False)
        self.assertIs(probe["orders"], False)
        self.assertIs(probe["shadow_only"], True)

        # Every explicit urllib Request method must be GET.
        methods = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "Request"):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "method"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    methods.append(keyword.value.value.upper())

        self.assertTrue(methods)
        self.assertEqual(set(methods), {"GET"})
        self.assertNotIn("order.place", source)
        self.assertNotIn("apiKey", source)

    def test_ui_bus_coalesces_identical_payload(self):
        bus = Bus()
        self.assertTrue(bus.publish("x", {"a": 1}))
        self.assertFalse(bus.publish("x", {"a": 1}))
        self.assertEqual(bus.coalesced, 1)
        self.assertEqual(bus.published, 1)

    def test_ui_health_is_read_only(self):
        bus = Bus()
        server = make_server(bus, ROOT / "a51/web/dashboard.html")
        try:
            client = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=2,
            )
            client.request("GET", "/health")
            response = client.getresponse()
            payload = json.loads(response.read())
            client.close()

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["read_only"])
            self.assertFalse(payload["orders"])
        finally:
            bus.closed = True
            with bus.cv:
                bus.cv.notify_all()
            server.shutdown()
            server.server_close()

    def test_json_cache_reuses_unchanged_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.json"
            path.write_text('{"x":1}\n')
            cache = JsonCache()
            self.assertEqual(cache.get(path), {"x": 1})
            self.assertEqual(cache.get(path), {"x": 1})
            self.assertEqual(
                (cache.view()["misses"], cache.view()["hits"]),
                (1, 1),
            )

    def test_paper_worker_hoists_repeated_reads(self):
        source = (ROOT / "a51/paper_worker.py").read_text()
        self.assertIn("analysis_blocked=cache.get", source)
        self.assertIn("analysis_health=cache.get", source)
        self.assertIn("heads=heads", source)
        self.assertNotIn(
            "analysis_bad=read_json(session/'ANALYSIS_BLOCKED.json',{}).get(s)",
            source,
        )

    def test_supervisor_cache_peak_and_satellite(self):
        source = (ROOT / "a51/supervisor.py").read_text()
        self.assertIn("self.cache=JsonCache()", source)
        self.assertIn("self.coverage_peak", source)
        self.assertIn(
            "self.launch('satellites','satellite_worker')",
            source,
        )

    def test_ui_isolated_dashboard_and_satellites(self):
        source = (ROOT / "a51/ui_worker.py").read_text().replace(" ", "")
        self.assertIn("ROOT/'a51/web/dashboard.html'", source)
        self.assertIn(
            "'satellites':session/'satellites/state.json'",
            source,
        )
        self.assertIn("coalesced_payloads", source)

    def test_projection_limits_display_only(self):
        source = (ROOT / "a51/terminal_projection.py").read_text().replace(" ", "")
        self.assertIn("CURVE_MAX_POINTS=240", source)
        self.assertIn("CLOSED_POSITION_HISTORY=60", source)
        self.assertIn("A51_BOUNDED_DISPLAY_NOT_LEDGER", source)

    def test_dashboard_satellite_panel(self):
        html = (ROOT / "a51/web/dashboard.html").read_text()
        self.assertIn('id="satelliteRows"', html)
        self.assertIn('id="satRibbon"', html)
        self.assertIn("Binance / Bybit / Kraken", html)

    def test_engine_member_set_matches_a5(self):
        left_root = ROOT / "engine_a5"
        right_root = ROOT / "engine_a51"
        left = {
            str(path.relative_to(left_root))
            for path in left_root.rglob("*")
            if path.is_file()
        }
        right = {
            str(path.relative_to(right_root))
            for path in right_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(left, right)

    def test_base_a5_manifest_preserved(self):
        manifest = json.loads(
            (ROOT / "BASELINE_A5_MANIFEST_SHA256.json").read_text()
        )
        self.assertEqual(manifest["schema"], "belial_a5_16q_package_v1")


if __name__ == "__main__":
    unittest.main()
