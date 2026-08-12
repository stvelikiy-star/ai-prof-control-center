from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))
MODULE_PATH = ORCH / "github_task_gateway_service.py"
SPEC = importlib.util.spec_from_file_location("chatgpt_ak_bermet_release_prepare_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load gateway service adapter")
service = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = service
SPEC.loader.exec_module(service)


def release_issue(number: int = 42, *, login: str = "stvelikiy-star", payload=None):
    contract = service.RELEASE_CONTRACT if payload is None else payload
    return {
        "number": number,
        "title": service.RELEASE_TITLE,
        "body": service.RELEASE_BODY_MARKER + json.dumps(contract),
        "user": {"login": login},
    }


class ChatGptAkBermetReleasePrepareTests(unittest.TestCase):
    def test_contract_and_authority_are_exact_and_not_issue_selectable(self):
        issue = release_issue()
        service.parse_release_prepare_contract(issue)
        self.assertEqual(service.RELEASE_PROJECT, "ak-bermet")
        self.assertEqual(service.RELEASE_PROFILE, "ak-bermet-production-prepare-v6")
        self.assertEqual(service.RELEASE_SCOPE, "README.md")
        changed = dict(service.RELEASE_CONTRACT)
        changed["action"] = "deploy"
        with self.assertRaisesRegex(service.gateway.GatewayError, "exactly match"):
            service.parse_release_prepare_contract(release_issue(payload=changed))

    def test_submit_uses_fixed_operations_profile_and_shell_false(self):
        payload = {"task_id": "AK_BERMET_20260812T120000Z_ABCDEF", "queue": "pending"}
        completed = subprocess.CompletedProcess(
            ["python3"], 0, json.dumps(payload), ""
        )
        with mock.patch.object(service.subprocess, "run", return_value=completed) as runner:
            actual = service.submit_release_prepare(42)
        self.assertEqual(actual, payload)
        argv = runner.call_args.args[0]
        self.assertIs(runner.call_args.kwargs["shell"], False)
        self.assertIn("--execution-mode", argv)
        self.assertEqual(argv[argv.index("--execution-mode") + 1], "operations")
        self.assertEqual(argv[argv.index("--operation-profile") + 1], service.RELEASE_PROFILE)
        self.assertEqual(argv[argv.index("--project") + 1], "ak-bermet")
        self.assertEqual(argv[argv.index("--base-branch") + 1], "main")
        self.assertEqual(argv[argv.index("--scope") + 1], "README.md")
        joined = " ".join(argv)
        self.assertNotIn(" db push ", f" {joined} ")
        self.assertNotIn(" db reset ", f" {joined} ")
        self.assertNotIn(" deploy ", f" {joined} ")

    def test_release_instructions_preserve_source_marker_for_exactly_once_recovery(self):
        text = service.release_prepare_instructions(77)
        self.assertIn(service.gateway.issue_marker(77), text)
        self.assertNotIn("\n", text)
        self.assertIn("Do not deploy", text)
        self.assertIn("Do not", text)

    def test_unauthorized_release_prepare_never_submits(self):
        state = {}
        issue = release_issue(login="attacker")
        with (
            mock.patch.object(service, "submit_release_prepare") as submit,
            mock.patch.object(service.gateway, "post_comment"),
        ):
            changed = service.process_release_prepare_issue(issue, state)
        self.assertTrue(changed)
        submit.assert_not_called()
        self.assertEqual(state["42"]["status"], "rejected")

    def test_valid_release_prepare_is_imported_once_and_reports_read_only_authority(self):
        state = {}
        created = {"task_id": "AK_BERMET_20260812T120000Z_ABCDEF", "queue": "pending"}
        with (
            mock.patch.object(service.gateway, "find_existing_task_for_issue", return_value=None),
            mock.patch.object(service, "submit_release_prepare", return_value=created) as submit,
            mock.patch.object(service.gateway, "post_comment") as comment,
        ):
            self.assertTrue(service.process_release_prepare_issue(release_issue(), state))
            self.assertFalse(service.process_release_prepare_issue(release_issue(), state))
        submit.assert_called_once_with(42)
        self.assertEqual(state["42"]["task_id"], created["task_id"])
        self.assertIn("read-only preparation only", comment.call_args.args[1])

    def test_install_adapter_preserves_ordinary_gateway_delegation(self):
        original = service.gateway.process_issue
        with mock.patch.object(service, "process_release_prepare_issue", return_value=True) as release_process:
            service.install_runtime_adapters()
            wrapped = service.gateway.process_issue
            self.assertTrue(wrapped(release_issue(), {}))
            release_process.assert_called_once()
        service.gateway.process_issue = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
