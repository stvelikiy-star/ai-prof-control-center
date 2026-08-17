#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import telegram_bridge_v3 as bridge


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


class TelegramTerminalNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.state_file = self.state / "terminal.json"
        self.client = FakeClient()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_task(
        self,
        queue: str,
        task_id: str,
        *,
        project_path: str = bridge.AK_BERMET_PROJECT_PATH,
        branch: str = "feature/telegram-0123abcd-012345abcdef",
        reason: str = "",
    ) -> Path:
        directory = self.state / "queue" / queue
        directory.mkdir(parents=True, exist_ok=True)
        lines = [
            f"Task-ID: {task_id}",
            f"Project-Path: {project_path}",
            "Goal: terminal notification test",
            f"Work-Branch: {branch}",
        ]
        if queue == "blocked" and reason:
            lines.append(f"Blocked-Reason: {reason}")
        if queue == "failed" and reason:
            lines.append(f"Failure-Reason: {reason}")
        path = directory / f"{task_id}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_publish_log(self, task_id: str, pr_number: int = 123) -> None:
        directory = self.state / "logs" / "orchestrator"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{task_id}-PUBLISH-20260817T000000Z.log").write_text(
            "\n".join(
                [
                    "APPROVED_TASK_PUBLISHER_PASS",
                    f"task_id={task_id}",
                    f"pr=https://github.com/stvelikiy-star/ak-bermet/pull/{pr_number}",
                    "merge_performed=false",
                    "deployment_performed=false",
                    "database_changed=false",
                    "secrets_accessed=false",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def initialize_empty_state(self) -> None:
        bridge._write_state(self.state_file, {})

    def test_first_start_seeds_history_without_replay(self) -> None:
        task_id = "AK_BERMET_20260817T000000Z_ABCDEF"
        self.write_task("blocked", task_id, reason="old historical blocker")
        sent = bridge.notify_terminal_changes(
            self.client,
            state_root=self.state,
            state_path=self.state_file,
        )
        self.assertEqual(sent, 0)
        self.assertEqual(self.client.messages, [])
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["tasks"][task_id], "blocked")

    def test_new_completed_code_task_waits_for_publish_log_then_sends_pr_once(self) -> None:
        self.initialize_empty_state()
        task_id = "AK_BERMET_20260817T000001Z_ABCDEF"
        self.write_task("completed", task_id)

        self.assertEqual(
            bridge.notify_terminal_changes(
                self.client,
                state_root=self.state,
                state_path=self.state_file,
            ),
            0,
        )
        self.assertEqual(self.client.messages, [])

        self.write_publish_log(task_id, 321)
        self.assertEqual(
            bridge.notify_terminal_changes(
                self.client,
                state_root=self.state,
                state_path=self.state_file,
            ),
            1,
        )
        self.assertEqual(len(self.client.messages), 1)
        self.assertIn("Result: PASS", self.client.messages[0])
        self.assertIn("/pull/321", self.client.messages[0])
        self.assertIn("Merge: not performed", self.client.messages[0])

        self.assertEqual(
            bridge.notify_terminal_changes(
                self.client,
                state_root=self.state,
                state_path=self.state_file,
            ),
            0,
        )
        self.assertEqual(len(self.client.messages), 1)

    def test_new_blocked_task_sends_redacted_terminal_reason_once(self) -> None:
        self.initialize_empty_state()
        task_id = "AK_BERMET_20260817T000002Z_ABCDEF"
        self.write_task("blocked", task_id, reason="required check failed")
        self.assertEqual(
            bridge.notify_terminal_changes(
                self.client,
                state_root=self.state,
                state_path=self.state_file,
            ),
            1,
        )
        self.assertEqual(len(self.client.messages), 1)
        self.assertIn("Result: BLOCKED", self.client.messages[0])
        self.assertIn("required check failed", self.client.messages[0])

    def test_non_ak_bermet_terminal_task_is_ignored(self) -> None:
        self.initialize_empty_state()
        task_id = "KOL_TRAVEL_PLATFORM_20260817T000003Z_ABCDEF"
        self.write_task(
            "blocked",
            task_id,
            project_path="/home/agent/Загрузки/kol-travel-platform",
            reason="not our notification scope",
        )
        self.assertEqual(
            bridge.notify_terminal_changes(
                self.client,
                state_root=self.state,
                state_path=self.state_file,
            ),
            0,
        )
        self.assertEqual(self.client.messages, [])

    def test_terminal_transition_is_notified_again_only_when_queue_changes(self) -> None:
        task_id = "AK_BERMET_20260817T000004Z_ABCDEF"
        bridge._write_state(self.state_file, {task_id: "failed"})
        self.write_task("completed", task_id, branch="feature/non-code-operation")
        self.assertEqual(
            bridge.notify_terminal_changes(
                self.client,
                state_root=self.state,
                state_path=self.state_file,
            ),
            1,
        )
        self.assertIn("Result: PASS", self.client.messages[0])


if __name__ == "__main__":
    unittest.main()
