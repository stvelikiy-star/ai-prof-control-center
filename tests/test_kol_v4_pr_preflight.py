from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"orchestrator"))
import kol_v4_pr_preflight as preflight


class KolPRPreflightTests(unittest.TestCase):
    def test_isolated_clone_is_registered_but_service_not_yet_authorized(self):
        result = preflight.checks()
        self.assertFalse(result["ready"])
        self.assertEqual(result["facts"]["isolated_git_metadata"], "directory")
        self.assertEqual(result["facts"]["registered_project_path"], str(preflight.ISOLATED))
        self.assertEqual(result["facts"]["registered_base_branch"], "main")
        self.assertTrue(any("служб" in item.lower() for item in result["reasons"]))

    def test_high_severity_audit_fails_closed_without_fixing_packages(self):
        class Result:
            returncode = 1
        with patch.object(preflight.subprocess, "run", return_value=Result()) as called:
            self.assertFalse(preflight._release_audit_ok(preflight.ISOLATED))
        called.assert_called_once()
        argv = called.call_args.args[0]
        self.assertEqual(argv[:2], ["npm", "audit"])
        self.assertIn("--audit-level=high", argv)
        self.assertNotIn("fix", argv)

    def test_registry_absent_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            state = preflight.checks(Path(directory) / "missing.json")
            self.assertFalse(state["ready"])
            self.assertIn("Реестр проектов не читается", state["reasons"])

    def test_no_shell_or_network_side_effects(self):
        with patch.object(preflight.subprocess, "run", return_value=type(
            "Result", (), {"stdout": "", "returncode": 1}
        )()) as mock_run:
            result = preflight.checks()
        self.assertFalse(result["ready"])
        for call in mock_run.call_args_list:
            if call.args[0][0] == "git":
                self.assertIn(call.args[0][3], ("branch", "status", "rev-parse", "remote"))
            elif call.args[0][0] == "/usr/bin/systemctl":
                self.assertEqual(call.args[0][1], "show")
            else:
                self.assertEqual(call.args[0][:2], ["npm", "audit"])
                self.assertNotIn("fix", call.args[0])
if __name__=="__main__":
    unittest.main()
