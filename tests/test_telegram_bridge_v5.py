#!/usr/bin/env python3
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import telegram_bridge as legacy
import telegram_bridge_v5 as v5
import ai_prof_readonly_audit as audit

class Client:
    def __init__(self):
        self.messages = []
    def send(self, value):
        self.messages.append(value)

class TelegramV5Tests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.config = legacy.Config(token="fake", chat_id=19, owner_user_id=19)
    def update(self, text):
        return {"message": {"text": text}}
    def test_approvals_is_owner_only_and_read_only(self):
        with patch.object(legacy, "authorized", return_value=True), \
             patch.object(v5, "owner_approvals_report",
                          return_value="OWNER_REVIEW_ONLY"), \
             patch.object(v5, "_ORIGINAL_EXTENDED") as previous:
            v5.handle_update_v5(self.update("/ai approvals"),
                                self.config, self.client)
            self.assertEqual(self.client.messages, ["OWNER_REVIEW_ONLY"])
            previous.assert_not_called()

    def test_non_owner_cannot_read_approvals(self):
        with patch.object(legacy, "authorized", return_value=False), \
             patch.object(v5, "owner_approvals_report") as report:
            v5.handle_update_v5(self.update("/ai approvals"),
                                self.config, self.client)
            report.assert_not_called()
            self.assertEqual(self.client.messages, [])

    def test_approvals_reports_evidence_without_mutating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            approved = root / "queue/approved"
            logs = root / "logs/orchestrator"
            approved.mkdir(parents=True)
            logs.mkdir(parents=True)
            task_id = "KOL_TRAVEL_PLATFORM_20261109T063719Z_7899A5"
            task = approved / (task_id + ".md")
            task.write_text(
                "Task-ID: " + task_id + "\n"
                "Project-Path: /safe/kol\n"
                "Instructions: token=REDACT_THIS_VALUE\n"
            )
            (logs / (task_id + "-01B-x.log")).write_text(
                "STAGE_01B_CODEX_PASS\n"
            )
            (logs / (task_id + "-01C-x.log")).write_text(
                "STAGE_01C_AUDIT_PASS\n"
            )
            with patch.object(v5.v2, "_projects", return_value={
                "kol-travel-platform": {"path": "/safe/kol"}
            }):
                value = v5.owner_approvals_report(root)
            self.assertIn("01B=PASS", value)
            self.assertIn("01C=PASS", value)
            self.assertIn("not published", value)
            self.assertNotIn("REDACT_THIS_VALUE", value)
            self.assertTrue(task.exists())

    def test_owner_audit_is_read_only(self):
        with patch.object(legacy, "authorized", return_value=True), patch.object(
            audit, "build_report", return_value="READ_ONLY_REPORT"
        ), patch.object(v5, "_ORIGINAL_EXTENDED") as prior:
            v5.handle_update_v5(self.update("/ai audit"), self.config, self.client)
            self.assertEqual(self.client.messages, ["READ_ONLY_REPORT"])
            prior.assert_not_called()
    def test_non_owner_cannot_trigger_audit(self):
        with patch.object(legacy, "authorized", return_value=False), patch.object(
            audit, "build_report"
        ) as report:
            v5.handle_update_v5(self.update("/ai audit"), self.config, self.client)
            report.assert_not_called()
            self.assertEqual(self.client.messages, [])
    def test_no_edit_audit_not_submitted_as_code(self):
        command = "/ai task ai-prof-control-center | Аудит системы | Проверь сервер. Ничего не изменяй."
        with patch.object(legacy, "authorized", return_value=True), patch.object(
            v5, "_ORIGINAL_EXTENDED"
        ) as prior:
            v5.handle_update_v5(self.update(command), self.config, self.client)
            self.assertIn("/ai audit", self.client.messages[0])
            prior.assert_not_called()
    def test_regular_code_task_preserved(self):
        with patch.object(legacy, "authorized", return_value=True), patch.object(
            v5, "_ORIGINAL_EXTENDED"
        ) as prior:
            update = self.update("/ai task ai-prof-control-center | Fix | Update README.md")
            v5.handle_update_v5(update, self.config, self.client)
            prior.assert_called_once_with(update, self.config, self.client)
    def test_other_project_readonly_is_not_mutated(self):
        with patch.object(legacy, "authorized", return_value=True), patch.object(
            v5, "_ORIGINAL_EXTENDED"
        ) as prior:
            update = self.update("/ai task ak-bermet | Аудит | Ничего не изменяй")
            v5.handle_update_v5(update, self.config, self.client)
            prior.assert_not_called()
            self.assertIn("No task was created", self.client.messages[0])
    def test_natural_language_audit_readonly(self):
        with patch.object(legacy, "authorized", return_value=True), patch.object(
            audit, "build_report", return_value="SNAPSHOT"
        ), patch.object(v5, "_ORIGINAL_EXTENDED") as prior:
            v5.handle_update_v5(self.update("AI PROF проверь сервер!"), self.config, self.client)
            self.assertEqual(self.client.messages, ["SNAPSHOT"])
            prior.assert_not_called()
    def test_unrecognized_natural_text_does_not_create_task(self):
        with patch.object(legacy, "authorized", return_value=True), patch.object(
            audit, "build_report"
        ) as report, patch.object(v5, "_ORIGINAL_EXTENDED") as prior:
            value = self.update("отправь письмо и удали файлы")
            v5.handle_update_v5(value, self.config, self.client)
            report.assert_not_called()
            prior.assert_called_once()
    def test_review_pass_does_not_claim_completion(self):
        source = {"T": ("active", "ai-prof-control-center", "")}
        target = {"T": ("approved", "ai-prof-control-center", "")}
        with patch.object(v5, "_ORIGINAL_CHANGE_MESSAGES",
                          return_value=["State: passed"]):
            messages = v5.task_change_messages_v5(source, target)
        self.assertIn("still pending", messages[0])
    def test_main_installs_handler_without_running_real_poll(self):
        import telegram_bridge_v2 as v2
        original_handler = v2.extended_handle_update
        original_messages = v2.task_change_messages
        try:
            with patch.object(v5.v4, "main", return_value=0) as start:
                self.assertEqual(v5.main(), 0)
                start.assert_called_once()
                self.assertIs(v2.extended_handle_update, v5.handle_update_v5)
                self.assertIs(v2.task_change_messages, v5.task_change_messages_v5)
        finally:
            v2.extended_handle_update = original_handler
            v2.task_change_messages = original_messages
    def test_pending_owner_review_does_not_read_task_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            approved = root / "queue/approved"
            approved.mkdir(parents=True)
            task = approved / "AI_PROF_CONTROL_CENTER_20261108T151005Z_37A93D.md"
            task.write_text(
                "Task-ID: AI_PROF_CONTROL_CENTER_20261108T151005Z_37A93D\n"
                "Instructions: TOKEN=NEVER_EXPOSE_THIS\n"
            )
            alerts = audit.pending_owner_review(root)
            self.assertEqual(len(alerts), 1)
            self.assertIn("owner review", alerts[0])
            self.assertNotIn("NEVER_EXPOSE_THIS", alerts[0])

    def test_df_compatible_disk_percentage(self):
        from unittest.mock import patch
        from collections import namedtuple
        Usage = namedtuple("Usage", "total used free")
        with patch.object(audit.shutil, "disk_usage",
                          return_value=Usage(116 * 1024**3, 77 * 1024**3, 34 * 1024**3)), \
             patch.object(audit, "service_state", return_value="PASS"), \
             patch.object(audit, "container_state", return_value="PASS"), \
             patch.object(audit, "n8n_readiness", return_value="PASS"), \
             patch.object(audit.bridge, "control_center_health", return_value="healthy"), \
             patch.object(audit, "queue_overview", return_value=({}, "none")):
            report = audit.build_report()
        self.assertIn("70% used (df-compatible)", report)

    def test_only_allowlisted_services(self):
        with self.assertRaises(ValueError):
            audit.service_state("ssh.service; rm -rf /")
    def test_queue_readonly_and_no_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "queue" / "completed" / "AI_PROF_CONTROL_CENTER_20261108T151005Z_37A93D.md"
            path.parent.mkdir(parents=True)
            path.write_text("Task-ID: AI_PROF_CONTROL_CENTER_20261108T151005Z_37A93D\n"
                            "Project-Path: /home/agent/projects/ai-prof-control-center-maintenance\n"
                            "Instructions: token=SHOULD_NOT_LEAK\n")
            counts, last = audit.queue_overview(root)
            self.assertEqual(counts.get("completed"), 1)
            self.assertNotIn("SHOULD_NOT_LEAK", last)

if __name__ == "__main__":
    unittest.main()
