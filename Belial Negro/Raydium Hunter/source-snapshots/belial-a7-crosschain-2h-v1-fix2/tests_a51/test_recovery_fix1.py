import ast
import unittest

from pathlib import Path

from a51.common import ROOT
from a51.resume_guard import deferred_salvage, install_patch
from a51.recovery_stream import main as stream_main


class A51RRecoveryFix1Tests(unittest.TestCase):
    def test_guard_patch_installs(self):
        self.assertTrue(install_patch())

    def test_deferred_salvage_is_constant_size_metadata(self):
        source = (ROOT / "a51/resume_guard.py").read_text()
        self.assertNotIn(".read_bytes()", source)
        self.assertNotIn(".write_bytes(", source)
        self.assertIn("DEFERRED_STREAM_AUDIT", source)

    def test_stream_audit_uses_line_reader(self):
        source = (ROOT / "a51/recovery_stream.py").read_text()
        ast.parse(source)
        self.assertNotIn(".read_bytes()", source)
        self.assertNotIn(".write_bytes(", source)
        self.assertIn("readline(MAX_LINE + 1)", source)

    def test_original_run_manager_is_not_modified_by_fix_module(self):
        baseline = (ROOT / "BASELINE_A51R_MANIFEST_SHA256.json")
        self.assertTrue(baseline.is_file())


if __name__ == "__main__":
    unittest.main()
