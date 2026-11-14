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
    def test_scope_guard_does_not_reject_real_edit_request(self):
        self.assertFalse(v5.is_readonly_intent(
            "Исправь баг", "Исправь Telegram бота, не изменяй базу данных",
        ))
        self.assertTrue(v5.is_readonly_intent(
            "Аудит", "Проверь сервер, ничего не изменяй",
        ))

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
    def test_private_start_enables_personal_updates(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        update = {"message": {"chat": {"id": 42, "type": "private"},
                              "from": {"id": 42}, "text": "/start"}}
        with patch.object(v5, "_owner_client", return_value=self.client), \
             patch.object(v5, "_enable_private_updates") as opt_in:
            v5.handle_update_v5(update, cfg, self.client)
        opt_in.assert_called_once()
        self.assertIn("Задача:", self.client.messages[0])

    def test_private_natural_task_uses_existing_allowlisted_intake(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        update = {"message": {"chat": {"id": 42, "type": "private"},
                              "from": {"id": 42},
                              "text": "Задача: исправь вывод в orchestrator/telegram_bridge.py"}}
        project = {"allowed_scope": ["README.md", "orchestrator/telegram_bridge.py"]}
        with patch.object(v5, "_owner_client", return_value=self.client), \
             patch.object(legacy, "load_projects", return_value={
                 "ai-prof-control-center": project,
             }), \
             patch.object(legacy, "submit", return_value=("TASK_TEST", "ai-prof-control-center")) as submit:
            v5.handle_update_v5(update, cfg, self.client)
        self.assertIn("TASK_TEST", self.client.messages[0])
        self.assertIn("сохранено в очереди", self.client.messages[0])
        self.assertEqual(submit.call_count, 1)
        submitted = submit.call_args.args[0]
        self.assertEqual(submitted.project, "ai-prof-control-center")
        self.assertEqual(submitted.instructions, "исправь вывод в orchestrator/telegram_bridge.py")

    def test_private_ambiguous_task_requests_scope_without_mutating(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        update = {"message": {"chat": {"id": 42, "type": "private"},
                              "from": {"id": 42},
                              "text": "Задача: сделай нашу систему максимально удобной"}}
        with patch.object(v5, "_owner_client", return_value=self.client), \
             patch.object(legacy, "load_projects", return_value={
                 "ai-prof-control-center": {"allowed_scope": ["README.md", "docs/**"]},
             }), \
             patch.object(legacy, "submit") as submit:
            v5.handle_update_v5(update, cfg, self.client)
        submit.assert_not_called()
        self.assertIn("не определил раздел", self.client.messages[0])

    def test_private_impersonation_gets_no_reply_or_task(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        update = {"message": {"chat": {"id": 42, "type": "private"},
                              "from": {"id": 54}, "text": "Задача: исправь код"}}
        with patch.object(v5, "_owner_client") as owner, \
             patch.object(legacy, "submit") as submit:
            v5.handle_update_v5(update, cfg, self.client)
        owner.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(self.client.messages, [])

    def test_group_short_task_command_uses_existing_handler(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        update = {"message": {"chat": {"id": -1001, "type": "group"},
                              "from": {"id": 42}, "text": "/task Исправь справку"}}
        with patch.object(v5, "_ORIGINAL_EXTENDED") as ext:
            v5.handle_update_v5(update, cfg, self.client)
        forwarded = ext.call_args.args[0]
        self.assertEqual(forwarded["message"]["text"], "/ai task Исправь справку")

    def test_private_notification_delivery_falls_back_to_group(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root)/"enabled"
            marker.write_text("owner-started\n")
            group = unittest.mock.Mock()
            group.config = legacy.Config("fake", -1001, 42)
            private = unittest.mock.Mock()
            with patch.object(v5, "PRIVATE_ENABLED_MARKER", marker), \
                 patch.object(v5, "_owner_client", return_value=private):
                notifier = v5._owner_notification_client(group)
                notifier.send("new result")
                private.send.assert_called_once_with("new result")
                group.send.assert_not_called()
                private.send.side_effect = legacy.BridgeError("private revoked")
                notifier.send("retry")
                group.send.assert_called_once_with("retry")

    def test_private_notification_opt_in_file_is_owner_started_only(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root)/"run"/"owner-started"
            with patch.object(v5, "PRIVATE_ENABLED_MARKER", marker):
                v5._enable_private_updates()
                self.assertEqual(marker.read_text(), "owner-started\n")
                self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

    def test_private_conversation_confirmed_scope_is_submitted_once(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        def update(text):
            return {"message": {"chat": {"id": 42, "type": "private"},
                                "from": {"id": 42}, "text": text}}
        project = {"allowed_scope": ["README.md", "orchestrator/telegram_bridge.py"]}
        with tempfile.TemporaryDirectory() as root, \
             patch.object(v5, "OWNER_DRAFT_FILE", Path(root)/"proposal.json"), \
             patch.object(v5, "_owner_client", return_value=self.client), \
             patch.object(legacy, "load_projects", return_value={
                 "ai-prof-control-center": project,
             }), \
             patch.object(legacy, "submit", return_value=("TASK_ACCEPTED", "ai-prof-control-center")) as submit:
            v5.handle_update_v5(update("Задача: улучши Telegram бота и исправь ответы"), cfg, self.client)
            self.assertIn("Запустить?", self.client.messages[-1])
            submit.assert_not_called()
            v5.handle_update_v5(update("Да"), cfg, self.client)
            submit.assert_called_once()
            cmd = submit.call_args.args[0]
            self.assertIn("orchestrator/telegram_bridge.py", cmd.instructions)
            self.assertIn("TASK_ACCEPTED", self.client.messages[-1])
            v5.handle_update_v5(update("Да"), cfg, self.client)
            submit.assert_called_once()

    def test_ambiguous_conversation_accepts_short_area_then_confirmation(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        def update(text):
            return {"message": {"chat": {"id": 42, "type": "private"},
                                "from": {"id": 42}, "text": text}}
        project = {"allowed_scope": ["orchestrator/telegram_bridge.py"]}
        with tempfile.TemporaryDirectory() as root, \
             patch.object(v5, "OWNER_DRAFT_FILE", Path(root)/"proposal.json"), \
             patch.object(v5, "_owner_client", return_value=self.client), \
             patch.object(legacy, "load_projects", return_value={
                 "ai-prof-control-center": project,
             }), \
             patch.object(legacy, "submit", return_value=("TASK_2", "ai-prof-control-center")) as submit:
            v5.handle_update_v5(update("Задача: улучшить рабочие разделы нашей системы"), cfg, self.client)
            self.assertIn("Что менять?", self.client.messages[-1])
            v5.handle_update_v5(update("Telegram"), cfg, self.client)
            self.assertIn("Запустить?", self.client.messages[-1])
            v5.handle_update_v5(update("Да"), cfg, self.client)
            submit.assert_called_once()

    def test_proposal_expires_and_cannot_submit(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root)/"proposal.json"
            with patch.object(v5, "OWNER_DRAFT_FILE", marker), \
                 patch.object(v5.time, "time", return_value=200_000):
                v5._save_proposal("исправить работу Telegram бота", "orchestrator/telegram_bridge.py")
            with patch.object(v5, "OWNER_DRAFT_FILE", marker), \
                 patch.object(v5.time, "time", return_value=300_000):
                self.assertIsNone(v5._load_proposal())

    def test_natural_task_other_client_project_is_rejected(self):
        cfg = legacy.Config(token="fake", chat_id=-1001, owner_user_id=42)
        update = {"message": {"chat": {"id": 42, "type": "private"},
                              "from": {"id": 42},
                              "text": "Задача: исправь MARINA в orchestrator/telegram_bridge.py"}}
        with patch.object(v5, "_owner_client", return_value=self.client), \
             patch.object(legacy, "submit") as submit:
            v5.handle_update_v5(update, cfg, self.client)
        submit.assert_not_called()
        self.assertIn("только AI PROF", self.client.messages[-1])

    def test_keyword_work_is_not_misrouted_as_bot(self):
        self.assertIsNone(v5._area_choice("Задача: улучшить работу системы"))
        self.assertEqual(v5._area_choice("Задача: исправь Telegram бота")[0], "телеграм")

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
