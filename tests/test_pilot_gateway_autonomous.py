from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import pilot_gateway_autonomous as g


def fixed_issue():
    body = (
        "AI-PROF-PILOT-E2E-V2\n"
        "Scope-Files: scripts/task_health.py, tests/test_task_health.py\n"
        "Base-Branch: main\n"
    )
    return {
        "number": 3, "state": "open", "user": {"login": "stvelikiy-star"},
        "title": g.TITLE, "body": body,
    }


class PilotGatewayAutonomousTests(unittest.TestCase):
    def setUp(self):
        self._original_core = {
            name: getattr(g.core, name)
            for name in (
                "ISSUE", "DIR", "STATE", "LOCK", "BRANCH", "MARKER",
                "EXPECTED_TITLE", "EXPECTED_BODY_SHA256", "INDEPENDENT_CHECKS",
                "PROMPT_SUFFIX",
            )
        }

    def tearDown(self):
        for name, value in self._original_core.items():
            setattr(g.core, name, value)

    def test_pinned_owner_and_scope(self):
        self.assertEqual(g.ISSUE_NUMBER, 3)
        self.assertEqual(g.BRANCH, "feature/ai-prof-pilot-issue-3")
        self.assertTrue(str(g.PILOT_DIR).endswith("ai-prof-pilot-auto-gateway"))
        self.assertIn("scripts/task_health.py", g.core.SCOPE)

    def test_issue_digest_mismatch_blocks(self):
        g.configure_core()
        with mock.patch.object(g.core, "github", return_value=fixed_issue()):
            with self.assertRaisesRegex(g.core.PilotError, "digest"):
                g.approved_issue()

    def test_wrong_owner_blocks(self):
        g.configure_core()
        issue = fixed_issue()
        issue["user"]["login"] = "untrusted"
        with mock.patch.object(g.core, "github", return_value=issue):
            with self.assertRaises(g.core.PilotError):
                g.approved_issue()

    def test_missing_authorization_cannot_trigger_codex(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(g, "PILOT_STATE", Path(td) / "missing.json"),
                mock.patch.object(g, "approved_issue", side_effect=g.core.PilotError("blocked")),
                mock.patch.object(g.core, "main") as run,
            ):
                self.assertEqual(g.run_once(), 2)
                run.assert_not_called()

    def test_failed_task_is_never_automatically_retried(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            p.write_text('{"phase":"failed"}')
            with mock.patch.object(g, "PILOT_STATE", p), mock.patch.object(g.core, "main") as run:
                self.assertEqual(g.run_once(), 0)
                run.assert_not_called()

    def test_completed_and_notified_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            p.write_text('{"phase":"passed","notified":true}')
            with mock.patch.object(g, "PILOT_STATE", p), mock.patch.object(g.core, "main") as run:
                self.assertEqual(g.run_once(), 0)
                run.assert_not_called()

    def test_pass_without_notification_only_retries_notification(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            p.write_text('{"phase":"passed","notified":false}')
            with mock.patch.object(g, "PILOT_STATE", p), mock.patch.object(g.core, "main", return_value=0) as run:
                original = sys.argv[:]
                try:
                    sys.argv = ["gateway"]
                    self.assertEqual(g.run_once(), 0)
                    run.assert_called_once()
                    self.assertEqual(sys.argv, ["gateway"])
                finally:
                    sys.argv = original

    def test_resource_gate_defers_before_codex(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(g, "PILOT_STATE", Path(td) / "none.json"),
                mock.patch.object(g, "approved_issue", return_value=True),
                mock.patch.object(g, "resource_gate", return_value=(False, "RAM")),
                mock.patch.object(g.core, "main") as run,
            ):
                self.assertEqual(g.run_once(), 0)
                run.assert_not_called()

    def test_unexpected_branch_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(g, "PILOT_STATE", Path(td) / "none.json"),
                mock.patch.object(g, "approved_issue", return_value=True),
                mock.patch.object(g, "resource_gate", return_value=(True, "ok")),
                mock.patch.object(g.core, "git", return_value="wrong"),
                mock.patch.object(g.core, "main") as run,
            ):
                self.assertEqual(g.run_once(), 2)
                run.assert_not_called()

    def test_exact_checkout_enables_one_worker(self):
        def fake_git(*argv):
            if argv == ("rev-parse", "main"):
                return g.EXPECTED_BASE_SHA
            if argv == ("branch", "--show-current"):
                return "develop"
            if argv == ("status", "--porcelain=v1"):
                return ""
            raise AssertionError(argv)
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(g, "PILOT_STATE", Path(td) / "none.json"),
                mock.patch.object(g, "approved_issue", return_value=True),
                mock.patch.object(g, "resource_gate", return_value=(True, "ok")),
                mock.patch.object(g.core, "git", side_effect=fake_git),
                mock.patch.object(g.core, "main", return_value=0) as run,
            ):
                self.assertEqual(g.run_once(), 0)
                run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
