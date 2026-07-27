from __future__ import annotations

import importlib.util
import logging
import sys
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
            bridge.Command("task", "ak-bermet", "Fix tests", "Fix tests"),
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

    def test_submit_task_arguments_are_a_list_and_deterministic(self):
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
        self.assertEqual(first, second)
        self.assertEqual(project, "pilot")
        self.assertIn(str(bridge.SUBMIT_TASK), first)
        self.assertEqual(first[first.index("--project") + 1], "pilot")
        self.assertEqual(first[first.index("--scope") + 1], "README.md")
        with mock.patch.object(bridge.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout='{"task_id":"PILOT_001"}')
            self.assertEqual(bridge.submit(command, projects), ("PILOT_001", "pilot"))
            self.assertIs(run.call_args.kwargs["shell"], False)
        with mock.patch.object(
            bridge.subprocess, "run", side_effect=bridge.subprocess.TimeoutExpired("x", 30),
        ):
            with self.assertRaisesRegex(bridge.BridgeError, "intake is unavailable"):
                bridge.submit(command, projects)

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
