from __future__ import annotations
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"orchestrator"))
import kol_v4_pr_preflight as preflight


class KolPRPreflightTests(unittest.TestCase):
    def check_with_isolated_registry(self, isolated=None):
        # CI must not depend on the host-only /home/agent project registry.
        # Use the exact restricted KÖL V4 shape in a disposable fixture.
        isolated = preflight.ISOLATED if isolated is None else isolated
        with tempfile.TemporaryDirectory() as temp:
            registry = Path(temp) / "projects.json"
            registry.write_text(json.dumps({"projects": [{
                "project_id": preflight.CHECKOUT_TARGET,
                "path": str(isolated),
                "base_branch": "main",
                "allow_commits": False,
                "allow_push": False,
                "allow_merge": False,
                "allow_deployment": False,
            }]}), encoding="utf-8")
            return preflight.checks(registry, isolated=isolated)

    def test_isolated_clone_is_registered_but_service_not_yet_authorized(self):
        result = self.check_with_isolated_registry()
        self.assertFalse(result["ready"])
        # CI has no live Ubuntu KÖL runner clone; it must fail closed rather
        # than assume host-local .git metadata exists in the test environment.
        actual = result["facts"]["isolated_git_metadata"]
        if (preflight.ISOLATED / ".git").is_dir():
            self.assertEqual(actual, "directory")
        else:
            self.assertEqual(actual, "missing")
            self.assertTrue(any("каталог .git" in reason for reason in result["reasons"]))
        self.assertEqual(result["facts"]["registered_project_path"], str(preflight.ISOLATED))
        self.assertEqual(result["facts"]["registered_base_branch"], "main")
        self.assertTrue(any("служб" in item.lower() for item in result["reasons"]))

    def test_missing_isolated_clone_must_remain_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            absent = Path(temporary) / "never-created"
            result = self.check_with_isolated_registry(isolated=absent)
            self.assertFalse(result["ready"])
            self.assertEqual(result["facts"]["isolated_git_metadata"], "missing")
            self.assertTrue(any("каталог .git" in reason for reason in result["reasons"]))

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
            result = self.check_with_isolated_registry()
        self.assertFalse(result["ready"])
        self.assertTrue(mock_run.called)
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
