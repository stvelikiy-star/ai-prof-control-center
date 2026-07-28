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

    def test_status_includes_campaign_safety_and_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            campaigns = state / "campaigns"
            campaigns.mkdir()
            (campaigns / "three-day.json").write_text(json.dumps({
                "campaign_id": "three-day", "state": "active",
                "completed_steps": 1, "current_step": "two",
                "deadline": "2026-01-04T00:00:00Z",
                "integration_branch": "integration/ak-bermet-3day",
                "plan": {"tasks": [{}, {}]},
            }), encoding="utf-8")
            message = bridge.status_message(state, {})
            self.assertIn("Campaign-ID: three-day", message)
            self.assertIn("State: active", message)
            self.assertIn("Progress: 1/2", message)
            self.assertIn("Current step: two", message)
            self.assertIn("UTC deadline: 2026-01-04T00:00:00Z", message)
            self.assertIn("Integration branch: integration/ak-bermet-3day", message)
            self.assertIn("Push disabled: yes", message)
            self.assertIn("Deployment disabled: yes", message)

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

    def test_project_resolution_is_exact_and_unknown_names_are_rejected(self):
        projects = {
            "ak-bermet": {
                "project_id": "ak-bermet",
                "path": "/home/agent/projects/ak-bermet",
            },
            "ak-bermet-pilot": {
                "project_id": "ak-bermet-pilot",
                "path": "/home/agent/projects/ak-bermet-agent-pilot",
            },
        }
        self.assertEqual(
            bridge.resolve_project("ak-bermet", projects)[1]["path"],
            "/home/agent/projects/ak-bermet",
        )
        self.assertEqual(
            bridge.resolve_project("ak-bermet-pilot", projects)[1]["path"],
            "/home/agent/projects/ak-bermet-agent-pilot",
        )
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
            "allowed_scope": [
                "README.md", "docs/**", "src/**", "tests/**",
                "supabase/migrations/**",
            ],
            "work_prefixes": ["feature/", "fix/"],
        }
        cases = {
            "Fix the checkout calculation": "src",
            "Add regression tests": "tests",
            "Update documentation": "docs",
            "Correct README": "README.md",
            "Add a Supabase migration for bookings": "supabase/migrations",
        }
        for instructions, expected in cases.items():
            command = bridge.parse_command(f"/ai task {instructions}")
            with self.subTest(instructions=instructions):
                self.assertEqual(
                    bridge.select_scope(command, "ak-bermet", project), expected,
                )

    def test_plain_task_defaults_to_exact_real_project(self):
        command = bridge.parse_command("/ai task Fix the booking form")
        self.assertEqual(command.project, "ak-bermet")
        projects = {
            "ak-bermet": {
                "project_id": "ak-bermet",
                "allowed_scope": ["src/**"],
                "work_prefixes": ["feature/"],
            },
            "ak-bermet-pilot": {
                "project_id": "ak-bermet-pilot",
                "allowed_scope": ["ai-system/**"],
                "work_prefixes": ["feature/"],
            },
        }
        args, project_id = bridge.submit_arguments(command, projects)
        self.assertEqual(project_id, "ak-bermet")
        self.assertEqual(args[args.index("--project") + 1], "ak-bermet")
        self.assertEqual(args[args.index("--scope") + 1], "src")

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
            (project_path / "src").mkdir()
            subprocess.run(["git", "add", "."], cwd=project_path, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=project_path, check=True)
            registry = {
                "version": 1,
                "projects": [{
                    "project_id": "ak-bermet",
                    "path": str(project_path),
                    "base_branch": "develop",
                    "work_prefixes": ["feature/", "fix/"],
                    "allowed_scope": ["README.md", "src/**"],
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
                    "--project", "ak-bermet", "--title", "Fix checkout",
                    "--instructions", "Fix checkout",
                ],
                cwd=root, text=True, capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("--work-branch", rejected.stderr)

            projects = {"ak-bermet": registry["projects"][0]}
            command = bridge.parse_command("/ai task Fix checkout")
            with (
                mock.patch.object(bridge, "ROOT", root),
                mock.patch.object(bridge, "STATE_DIR", state),
            ):
                task_id, project_id = bridge.submit(command, projects)
            self.assertEqual(project_id, "ak-bermet")
            self.assertRegex(task_id, r"^AK_BERMET_")
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
        for secret in (
            "password=hunter2",
            "DATABASE_URL=postgresql://admin:private@db.example/app",
            "redis://user:private@cache.example/0",
            "sk-abcdefghijklmnopqrstuvwxyz123456",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn("private", bridge.redact(secret))
                self.assertNotIn("hunter2", bridge.redact(secret))
                self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", bridge.redact(secret))
        record = logging.LogRecord("test", logging.ERROR, "", 0, "token=%s", (token,), None)
        bridge.RedactingFilter().filter(record)
        self.assertNotIn(token, record.getMessage())

    def test_recent_tasks_maps_every_public_status_latest_first_and_limits_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            projects = {"pilot": {"path": "/projects/pilot"}}
            cases = (
                ("pending", "queued"), ("active", "running"),
                ("approved", "passed"), ("failed", "failed"),
                ("blocked", "blocked"), ("cancelled", "cancelled"),
            )
            for index, (queue, _status) in enumerate(cases, 1):
                task_id = f"PILOT_20260727T12000{index}Z_A{index}"
                directory = state / "queue" / queue
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"{task_id}.md").write_text(
                    f"Task-ID: {task_id}\nProject-Path: /projects/pilot\n"
                    f"Goal: title {index}\nInstructions: full private prompt {index}\n",
                    encoding="utf-8",
                )
            tasks = bridge.recent_tasks(state, projects, limit=10)
            self.assertEqual([task["state"] for task in tasks], [
                status for _queue, status in reversed(cases)
            ])
            self.assertEqual(len(bridge.recent_tasks(state, projects)), 5)
            self.assertNotIn("full private prompt", json.dumps(tasks))

    def test_internal_queue_states_map_to_queued_and_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for index, (queue, expected) in enumerate((
                ("review", "correction"), ("pending_codex", "codex audit"),
                ("completed", "passed"),
            )):
                task_id = f"TASK_20260727T13000{index}Z_X"
                directory = state / "queue" / queue
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"{task_id}.md").write_text(
                    f"Task-ID: {task_id}\nGoal: test\n", encoding="utf-8",
                )
            self.assertEqual(
                {task["state"] for task in bridge.recent_tasks(state, {}, 10)},
                {"correction", "codex audit", "passed"},
            )

    def test_passed_task_hides_obsolete_blocker_from_older_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            task_id = "TASK_20260727T130000Z_DONE"
            completed = state / "queue/completed"
            logs = state / "logs/orchestrator"
            completed.mkdir(parents=True)
            logs.mkdir(parents=True)
            (completed / f"{task_id}.md").write_text(
                f"Task-ID: {task_id}\nGoal: completed\n", encoding="utf-8",
            )
            (logs / f"{task_id}-01C-old.log").write_text(
                "BLOCKED_CODEX_LAUNCH\n", encoding="utf-8",
            )
            task = bridge.recent_tasks(state, {}, 1)[0]
            self.assertEqual(task["state"], "passed")
            self.assertEqual(task["result"], "PASS")
            self.assertNotIn("BLOCKED CODEX LAUNCH", bridge.status_message(state, {}))

    def test_terminal_reason_is_structured_and_malformed_files_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            task_id = "PILOT_20260727T140000Z_SAFE"
            queue = state / "queue/failed"
            logs = state / "logs/orchestrator"
            queue.mkdir(parents=True)
            logs.mkdir(parents=True)
            (queue / f"{task_id}.md").write_bytes(b"\xff malformed")
            (logs / f"{task_id}-01B.log").write_text(
                "full prompt: do not expose\n"
                "DATABASE_URL=postgresql://admin:secret@db/app\n"
                "CLAUDE_FAILED\n",
                encoding="utf-8",
            )
            message = bridge.status_message(state, {})
            self.assertIn("failed", message)
            self.assertNotIn("CLAUDE FAILED", message)
            self.assertNotIn("full prompt", message)
            self.assertNotIn("secret", message)

    def test_current_queue_supersedes_stale_history_for_all_nonterminal_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            cases = (
                ("active", "running", "BLOCKED_OLD"),
                ("pending_codex", "codex audit", "CLAUDE_FAILED"),
            )
            for index, (queue_name, expected, stale) in enumerate(cases):
                task_id = f"TASK_20260727T15000{index}Z_CURRENT"
                queue = state / "queue" / queue_name
                logs = state / "logs/orchestrator"
                queue.mkdir(parents=True, exist_ok=True)
                logs.mkdir(parents=True, exist_ok=True)
                (queue / f"{task_id}.md").write_text(
                    f"Task-ID: {task_id}\nGoal: current\nInstructions: old {stale}\n",
                    encoding="utf-8",
                )
                (logs / f"{task_id}-old.log").write_text(stale, encoding="utf-8")
                task = next(item for item in bridge.recent_tasks(state, {}, 10)
                            if item["id"] == task_id)
                self.assertEqual(task["state"], expected)
                self.assertEqual(task["result"], "")
            message = bridge.status_message(state, {})
            self.assertNotIn("BLOCKED OLD", message)
            self.assertNotIn("CLAUDE FAILED", message)

    def test_completed_old_fail_text_always_displays_pass_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            task_id = "TASK_20260727T160000Z_PASS"
            queue = state / "queue/completed"
            queue.mkdir(parents=True)
            (queue / f"{task_id}.md").write_text(
                f"Task-ID: {task_id}\nGoal: done\nInstructions: old FAIL and BLOCKED_CODEX\n",
                encoding="utf-8",
            )
            message = bridge.status_message(state, {})
            self.assertIn("passed | PASS", message)
            self.assertNotIn("FAIL", message)
            self.assertNotIn("BLOCKED CODEX", message)

    def test_blocked_uses_current_structured_reason_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            task_id = "TASK_20260727T170000Z_BLOCKED"
            queue = state / "queue/blocked"
            logs = state / "logs/orchestrator"
            queue.mkdir(parents=True)
            logs.mkdir(parents=True)
            (queue / f"{task_id}.md").write_text(
                f"Task-ID: {task_id}\nGoal: blocked\n"
                "Blocked-Reason: BLOCKED_CURRENT_ACCESS\n",
                encoding="utf-8",
            )
            (logs / f"{task_id}-old.log").write_text("BLOCKED_OLD", encoding="utf-8")
            message = bridge.status_message(state, {})
            self.assertIn("blocked | BLOCKED_CURRENT_ACCESS", message)
            self.assertNotIn("BLOCKED_OLD", message)

    def test_duplicate_task_id_reports_queue_inconsistency_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            task_id = "TASK_20260727T180000Z_DUP"
            for queue_name in ("pending", "active"):
                queue = state / "queue" / queue_name
                queue.mkdir(parents=True)
                (queue / f"{task_id}.md").write_text(
                    f"Task-ID: {task_id}\nGoal: duplicate\n", encoding="utf-8",
                )
            tasks = bridge.recent_tasks(state, {}, 10)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(
                tasks[0]["state"], "QUEUE INCONSISTENCY (active, pending)",
            )

    def test_campaign_current_task_comes_from_fresh_queue_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            task_id = "TASK_20260727T190000Z_CAMPAIGN"
            queue = state / "queue/active"
            campaigns = state / "campaigns"
            queue.mkdir(parents=True)
            campaigns.mkdir()
            (queue / f"{task_id}.md").write_text(
                f"Task-ID: {task_id}\nGoal: campaign\n", encoding="utf-8",
            )
            campaign_file = campaigns / "live.json"
            campaign_file.write_text(json.dumps({
                "campaign_id": "live", "state": "active",
                "completed_steps": 2, "current_step": "verify",
                "current_task_id": task_id, "deadline": "2026-07-29T00:00:00Z",
                "integration_branch": "integration/live",
                "plan": {"tasks": [{}, {}, {}]},
            }), encoding="utf-8")
            first = bridge.status_message(state, {})
            self.assertIn(f"Current Task-ID: {task_id} | queue state: running", first)
            queue.rename(state / "queue/completed")
            campaign_file.write_text(json.dumps({
                "campaign_id": "live", "state": "complete",
                "completed_steps": 3, "current_step": None,
                "current_task_id": task_id, "deadline": "2026-07-29T00:00:00Z",
                "integration_branch": "integration/live",
                "plan": {"tasks": [{}, {}, {}]},
            }), encoding="utf-8")
            second = bridge.status_message(state, {})
            self.assertIn("State: complete", second)
            self.assertIn("Progress: 3/3", second)
            self.assertIn(f"Current Task-ID: {task_id} | queue state: passed", second)

    def test_status_handler_preserves_authorization_and_uses_runtime_report(self):
        client = mock.Mock()
        message = {
            "chat": {"id": -1001}, "from": {"id": 42}, "text": "/ai status",
        }
        with (
            mock.patch.object(bridge, "load_projects", return_value={}),
            mock.patch.object(bridge, "status_message", return_value="runtime status") as report,
        ):
            bridge.handle_update({"message": message}, self.config, client)
        report.assert_called_once_with(bridge.STATE_DIR, {})
        client.send.assert_called_once_with("runtime status")

    def test_control_center_health_handles_running_stopped_and_malformed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            run = state / "run"
            run.mkdir()
            self.assertEqual(bridge.control_center_health(state), "stopped")
            lock = (run / "supervisor.lock").open("w", encoding="utf-8")
            bridge.fcntl.flock(lock, bridge.fcntl.LOCK_EX | bridge.fcntl.LOCK_NB)
            try:
                (run / "heartbeat.json").write_text(
                    '{"timestamp":"2999-01-01T00:00:00+00:00"}', encoding="utf-8",
                )
                self.assertEqual(bridge.control_center_health(state), "healthy")
                (run / "heartbeat.json").write_text("{bad", encoding="utf-8")
                self.assertEqual(
                    bridge.control_center_health(state), "degraded (invalid heartbeat)",
                )
            finally:
                bridge.fcntl.flock(lock, bridge.fcntl.LOCK_UN)
                lock.close()

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
