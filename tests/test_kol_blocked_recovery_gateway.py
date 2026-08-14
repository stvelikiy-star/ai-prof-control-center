from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))
MODULE_PATH = ORCH / "github_task_gateway_service.py"
SPEC = importlib.util.spec_from_file_location("kol_blocked_recovery_gateway_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load gateway service adapter")
service = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = service
SPEC.loader.exec_module(service)


def recovery_issue(number: int = 62, *, login: str = "stvelikiy-star", payload=None):
    contract = service.KOL_RECOVERY_CONTRACT if payload is None else payload
    return {
        "number": number,
        "title": service.KOL_RECOVERY_TITLE,
        "body": service.KOL_RECOVERY_BODY_MARKER + json.dumps(contract),
        "user": {"login": login},
    }


class KolBlockedRecoveryGatewayTests(unittest.TestCase):
    def test_contract_is_exact_and_not_issue_selectable(self):
        service.parse_kol_recovery_contract(recovery_issue())
        changed = dict(service.KOL_RECOVERY_CONTRACT)
        changed["action"] = "reset-hard"
        with self.assertRaisesRegex(service.gateway.GatewayError, "exactly match"):
            service.parse_kol_recovery_contract(recovery_issue(payload=changed))

    def test_recovery_runs_only_fixed_script_with_shell_false(self):
        output = "\n".join(
            [
                "KOL_BLOCKED_RECOVERY=PASS",
                "KOL_HEAD=abc123",
                "RECOVERY_STASH=stash@{0}",
                "SOURCE_CHANGES_DELETED=NO",
                "DATABASE_CHANGED=NO",
                "DEPLOYMENT_PERFORMED=NO",
            ]
        )
        completed = subprocess.CompletedProcess(["python3"], 0, output, "")
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "recover.py"
            script.write_text("print('test')\n", encoding="utf-8")
            with (
                mock.patch.object(service, "validate_kol_recovery_script", return_value=script),
                mock.patch.object(service.subprocess, "run", return_value=completed) as runner,
            ):
                passed, detail = service.run_kol_recovery()
        self.assertTrue(passed)
        self.assertIn("KOL_BLOCKED_RECOVERY=PASS", detail)
        argv = runner.call_args.args[0]
        self.assertEqual(argv, [sys.executable, str(script)])
        self.assertIs(runner.call_args.kwargs["shell"], False)
        self.assertFalse(any("reset" in item for item in argv))
        self.assertFalse(any("supabase" in item.lower() for item in argv))

    def test_recovery_requires_all_no_mutation_postconditions(self):
        output = "\n".join(
            [
                "KOL_BLOCKED_RECOVERY=PASS",
                "SOURCE_CHANGES_DELETED=NO",
                "DATABASE_CHANGED=NO",
            ]
        )
        completed = subprocess.CompletedProcess(["python3"], 0, output, "")
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "recover.py"
            script.write_text("print('test')\n", encoding="utf-8")
            with (
                mock.patch.object(service, "validate_kol_recovery_script", return_value=script),
                mock.patch.object(service.subprocess, "run", return_value=completed),
            ):
                passed, _detail = service.run_kol_recovery()
        self.assertFalse(passed)

    def test_unauthorized_recovery_never_executes(self):
        state = {}
        with (
            mock.patch.object(service, "run_kol_recovery") as recovery,
            mock.patch.object(service.gateway, "post_comment"),
        ):
            changed = service.process_kol_recovery_issue(
                recovery_issue(login="attacker"), state
            )
        self.assertTrue(changed)
        recovery.assert_not_called()
        self.assertEqual(state["62"]["status"], "rejected")

    def test_valid_recovery_executes_exactly_once(self):
        state = {}
        detail = "\n".join(
            [
                "KOL_BLOCKED_RECOVERY=PASS",
                "SOURCE_CHANGES_DELETED=NO",
                "DATABASE_CHANGED=NO",
                "DEPLOYMENT_PERFORMED=NO",
            ]
        )
        with (
            mock.patch.object(service, "run_kol_recovery", return_value=(True, detail)) as recovery,
            mock.patch.object(service.gateway, "post_comment") as comment,
        ):
            self.assertTrue(service.process_kol_recovery_issue(recovery_issue(), state))
            self.assertFalse(service.process_kol_recovery_issue(recovery_issue(), state))
        recovery.assert_called_once_with()
        self.assertEqual(state["62"]["status"], "completed")
        self.assertEqual(state["62"]["kind"], service.KOL_RECOVERY_RECORD_KIND)
        self.assertIn("KÖL recovery PASS", comment.call_args.args[1])

    def test_install_adapter_routes_fixed_recovery_issue(self):
        original_process = service.gateway.process_issue
        original_report = service.gateway.report_task_state
        with mock.patch.object(service, "process_kol_recovery_issue", return_value=True) as recovery_process:
            service.install_runtime_adapters()
            wrapped = service.gateway.process_issue
            self.assertTrue(wrapped(recovery_issue(), {}))
            recovery_process.assert_called_once()
        service.gateway.process_issue = original_process
        service.gateway.report_task_state = original_report


if __name__ == "__main__":
    unittest.main(verbosity=2)
