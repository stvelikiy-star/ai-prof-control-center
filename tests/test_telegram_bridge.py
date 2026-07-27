from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator/telegram_bridge.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_telegram_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load telegram bridge")
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class TelegramBridgeTests(unittest.TestCase):
    def setUp(self):
        self.config = bridge.Config("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd", -1001, 42)

    def test_authorization_requires_both_ids(self):
        valid = {"chat": {"id": -1001}, "from": {"id": 42}, "text": "/ai help"}
        self.assertTrue(bridge.authorized(valid, self.config))
        for message in (
            {"chat": {"id": -1002}, "from": {"id": 42}},
            {"chat": {"id": -1001}, "from": {"id": 43}},
            {"chat": {"id": -1001}},
            {},
        ):
            with self.subTest(message=message):
                self.assertFalse(bridge.authorized(message, self.config))

    def test_command_parsing_and_ordinary_messages(self):
        self.assertIsNone(bridge.parse_command("daily report complete"))
        self.assertEqual(bridge.parse_command("/ai help"), bridge.Command("help"))
        self.assertEqual(bridge.parse_command("/ai status"), bridge.Command("status"))
        self.assertEqual(
            bridge.parse_command("/ai task Fix tests"),
            bridge.Command("task", "ak-bermet", "Fix tests", "Fix tests", True),
        )
        self.assertEqual(
            bridge.parse_command("/ai task ai-prof-pilot | Title | Instructions"),
            bridge.Command("task", "ai-prof-pilot", "Title", "Instructions"),
        )
        self.assertEqual(bridge.parse_command("/ai task one | two"), bridge.Command("invalid"))

    def test_project_validation_exact_and_default_alias(self):
        projects = {"ak-bermet-pilot": {"project_id": "ak-bermet-pilot"}}
        self.assertEqual(bridge.resolve_project("ak-bermet", projects)[0], "ak-bermet-pilot")
        with self.assertRaises(bridge.BridgeError):
            bridge.resolve_project("other", projects)

    def test_submit_task_arguments_are_complete_list_and_branches_are_unique(self):
        command = bridge.Command("task", "pilot", "Title", "Instructions")
        projects = {
            "pilot": {
                "project_id": "pilot",
                "allowed_scope": ["README.md", "docs/**"],
                "work_prefixes": ["feature/", "fix/"],
            }
        }
        first, project = bridge.submit_arguments(command, projects)
        second, _ = bridge.submit_arguments(command, projects)
        self.assertNotEqual(
            first[first.index("--work-branch") + 1],
            second[second.index("--work-branch") + 1],
        )
        self.assertEqual(project, "pilot")
        self.assertEqual(first[0], sys.executable)
        self.assertIn(str(bridge.SUBMIT_TASK), first)
        self.assertEqual(first[first.index("--root") + 1], str(bridge.ROOT))
        self.assertEqual(first[first.index("--state-root") + 1], str(bridge.STATE_DIR))
        self.assertEqual(first[first.index("--project") + 1], "pilot")
        self.assertEqual(first[first.index("--scope") + 1], "README.md")
        self.assertRegex(
            first[first.index("--work-branch") + 1],
            r"^feature/telegram-[0-9a-f]{8}-[0-9a-f]{12}$",
        )
        with mock.patch.object(bridge.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout='{"task_id":"PILOT_001"}')
            self.assertEqual(bridge.submit(command, projects), ("PILOT_001", "pilot"))
            self.assertIs(run.call_args.kwargs["shell"], False)
            self.assertEqual(run.call_args.kwargs["cwd"], bridge.ROOT)
        with mock.patch.object(
            bridge.subprocess, "run", side_effect=bridge.subprocess.TimeoutExpired("x", 30),
        ):
            with self.assertRaisesRegex(bridge.BridgeError, "intake is unavailable"):
                bridge.submit(command, projects)

    def test_plain_ak_bermet_scope_is_narrow_and_request_aware(self):
        project = {
            "allowed_scope": ["README.md", "docs/**", "ai-system/**", "tests/**"],
            "work_prefixes": ["feature/", "fix/"],
        }
        cases = {
            "Fix the checkout calculation": "ai-system",
            "Add regression tests": "tests",
            "Update documentation": "docs",
            "Correct README": "README.md",
        }
        for instructions, expected in cases.items():
            command = bridge.parse_command(f"/ai task {instructions}")
            with self.subTest(instructions=instructions):
                self.assertEqual(
                    bridge.select_scope(command, "ak-bermet-pilot", project), expected,
                )

    def test_real_cli_reproduces_missing_contract_rejection_then_accepts_bridge_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "control"
            project_path = Path(tmp) / "project"
            state = Path(tmp) / "state"
            (root / "orchestrator").mkdir(parents=True)
            (root / "agents/pilot").mkdir(parents=True)
            project_path.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=project_path, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=project_path, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=project_path, check=True,
            )
            (project_path / "README.md").write_text("test\n", encoding="utf-8")
            (project_path / "ai-system").mkdir()
            subprocess.run(["git", "add", "."], cwd=project_path, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=project_path, check=True)
            registry = {
                "version": 1,
                "projects": [{
                    "project_id": "ak-bermet-pilot",
                    "path": str(project_path),
                    "base_branch": "develop",
                    "work_prefixes": ["feature/", "fix/"],
                    "allowed_scope": ["README.md", "ai-system/**"],
                    "agent_context": "agents/pilot",
                    "allow_commits": False,
                    "allow_push": False,
                    "allow_merge": False,
                    "allow_deployment": False,
                }],
            }
            (root / "orchestrator/projects.json").write_text(
                json.dumps(registry), encoding="utf-8",
            )

            # Deterministically reproduce the production failure class: the
            # current create CLI rejects an invocation missing its mandatory contract.
            rejected = subprocess.run(
                [
                    sys.executable, str(bridge.SUBMIT_TASK),
                    "--root", str(root), "--state-root", str(state), "create",
                    "--project", "ak-bermet-pilot", "--title", "Fix checkout",
                    "--instructions", "Fix checkout",
                ],
                cwd=root, text=True, capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("--work-branch", rejected.stderr)

            projects = {"ak-bermet-pilot": registry["projects"][0]}
            command = bridge.parse_command("/ai task Fix checkout")
            with (
                mock.patch.object(bridge, "ROOT", root),
                mock.patch.object(bridge, "STATE_DIR", state),
            ):
                task_id, project_id = bridge.submit(command, projects)
            self.assertEqual(project_id, "ak-bermet-pilot")
            self.assertRegex(task_id, r"^AK_BERMET_PILOT_")
            self.assertTrue((state / "queue/pending" / f"{task_id}.md").is_file())

    def test_rejection_reason_is_sanitized_and_reported(self):
        command = bridge.Command("task", "pilot", "Title", "Instructions")
        projects = {
            "pilot": {
                "project_id": "pilot",
                "allowed_scope": ["README.md"],
                "work_prefixes": ["feature/"],
            },
        }
        token = self.config.token
        result = mock.Mock(
            returncode=2,
            stdout=json.dumps({"error": f"title invalid token={token}\ninternal detail"}),
        )
        with mock.patch.object(bridge.subprocess, "run", return_value=result):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.submit(command, projects)
        message = str(caught.exception)
        self.assertIn("title invalid", message)
        self.assertNotIn(token, message)
        self.assertNotIn("\n", message)

    def test_secret_redaction_in_plain_text_and_logs(self):
        token = self.config.token
        self.assertNotIn(token, bridge.redact(f"request failed at bot{token}/getUpdates"))
        record = logging.LogRecord("test", logging.ERROR, "", 0, "token=%s", (token,), None)
        bridge.RedactingFilter().filter(record)
        self.assertNotIn(token, record.getMessage())

    def test_unauthorized_and_ordinary_updates_send_nothing(self):
        client = mock.Mock()
        bridge.handle_update(
            {"message": {"chat": {"id": -1001}, "from": {"id": 43}, "text": "/ai help"}},
            self.config, client,
        )
        bridge.handle_update(
            {"message": {"chat": {"id": -1001}, "from": {"id": 42}, "text": "report"}},
            self.config, client,
        )
        client.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
