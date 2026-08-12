from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "orchestrator" / "telegram_bridge.py"
V2_PATH = ROOT / "orchestrator" / "telegram_bridge_v2.py"

legacy_spec = importlib.util.spec_from_file_location("telegram_bridge", LEGACY_PATH)
if legacy_spec is None or legacy_spec.loader is None:
    raise RuntimeError("cannot load legacy telegram bridge")
legacy = importlib.util.module_from_spec(legacy_spec)
sys.modules["telegram_bridge"] = legacy
legacy_spec.loader.exec_module(legacy)

v2_spec = importlib.util.spec_from_file_location("ai_prof_telegram_bridge_v2", V2_PATH)
if v2_spec is None or v2_spec.loader is None:
    raise RuntimeError("cannot load Telegram V2")
v2 = importlib.util.module_from_spec(v2_spec)
sys.modules[v2_spec.name] = v2
v2_spec.loader.exec_module(v2)


class FakeClient:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, text: str):
        self.messages.append(text)


class TelegramBridgeV2Tests(unittest.TestCase):
    def setUp(self):
        self.config = legacy.Config("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd", -1001, 42)

    def test_help_exposes_diagnostics_and_reasserts_safety(self):
        self.assertIn("/ai health", v2.V2_HELP)
        self.assertIn("/ai queue [project]", v2.V2_HELP)
        self.assertIn("/ai logs <TASK_ID>", v2.V2_HELP)
        self.assertIn("/ai blockers <project>", v2.V2_HELP)
        self.assertIn("/ai git <project> status", v2.V2_HELP)
        self.assertIn("no force-push", v2.V2_HELP.lower())
        self.assertIn("no migration", v2.V2_HELP.lower())
        self.assertIn("no deploy", v2.V2_HELP.lower())

    def test_unauthorized_update_is_silent(self):
        client = FakeClient()
        update = {"message": {"chat": {"id": -1001}, "from": {"id": 99}, "text": "/ai health"}}
        v2.extended_handle_update(update, self.config, client)
        self.assertEqual(client.messages, [])

    def test_health_command_is_owner_only_and_sent(self):
        client = FakeClient()
        update = {"message": {"chat": {"id": -1001}, "from": {"id": 42}, "text": "/ai health"}}
        with mock.patch.object(v2, "health_message", return_value="HEALTH PASS"):
            v2.extended_handle_update(update, self.config, client)
        self.assertEqual(client.messages, ["HEALTH PASS"])

    def test_unknown_v2_command_delegates_to_legacy_handler(self):
        client = FakeClient()
        update = {"message": {"chat": {"id": -1001}, "from": {"id": 42}, "text": "/ai status"}}
        delegate = mock.Mock()
        legacy.handle_update_original = delegate
        v2.extended_handle_update(update, self.config, client)
        delegate.assert_called_once_with(update, self.config, client)

    def test_git_status_uses_fixed_read_only_commands(self):
        with tempfile.TemporaryDirectory(prefix="ai-prof-v2-git-") as tmp:
            repo = Path(tmp) / "repo"
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "ci@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "CI"], check=True)
            (repo / "README.md").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "-C", repo, "add", "README.md"], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "fixture"], check=True)
            with mock.patch.object(v2, "_projects", return_value={
                "demo": {"path": str(repo), "base_branch": "master"}
            }), mock.patch.object(v2, "_run_readonly", wraps=v2._run_readonly) as run:
                text = v2.git_status("demo")
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(["git", "branch", "--show-current"], commands)
            self.assertIn(["git", "status", "--short", "--branch"], commands)
            flattened = " ".join(" ".join(cmd) for cmd in commands)
            for forbidden in ("reset", "clean", "commit", "push", "merge", "checkout"):
                self.assertNotIn(forbidden, flattened)
            self.assertIn("Worktree: clean", text)

    def test_readonly_runner_never_uses_shell_and_disables_git_locks(self):
        completed = subprocess.CompletedProcess(["git"], 0, "ok\n", "")
        with mock.patch.object(v2.subprocess, "run", return_value=completed) as run:
            code, output = v2._run_readonly(["git", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(output, "ok")
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")

    def test_task_log_output_is_redacted(self):
        task_id = "AK_BERMET_20260811T123222Z_E38EA5"
        with tempfile.TemporaryDirectory(prefix="ai-prof-v2-logs-") as tmp:
            log = Path(tmp) / f"{task_id}.log"
            log.write_text("password=SUPERSECRET\nnormal line\n", encoding="utf-8")
            with mock.patch.object(v2, "_candidate_logs", return_value=[log]):
                text = v2.task_logs(task_id)
            self.assertNotIn("SUPERSECRET", text)
            self.assertIn("[REDACTED]", text)
            self.assertIn("normal line", text)

    def test_queue_filter_and_blockers_do_not_require_shell_input(self):
        projects = {
            "demo": {"path": "/tmp/demo", "base_branch": "main"},
            "other": {"path": "/tmp/other", "base_branch": "main"},
        }
        locations = {
            "DEMO_20260812T100000Z_ABCDEF": [("blocked", Path("a.md"), {"Project-Path": "/tmp/demo", "Result": "BLOCKED_TEST"})],
            "OTHER_20260812T100001Z_ABCDEF": [("failed", Path("b.md"), {"Project-Path": "/tmp/other", "Result": "FAILED_TEST"})],
        }
        with mock.patch.object(v2, "_projects", return_value=projects), \
             mock.patch.object(legacy, "_queue_locations", return_value=locations), \
             mock.patch.object(legacy, "_task_time", return_value=1.0), \
             mock.patch.object(legacy, "_terminal_reason", side_effect=lambda q, f: f.get("Result", "")):
            text = v2.queue_message("demo")
        self.assertIn("DEMO_", text)
        self.assertNotIn("OTHER_", text)

    def test_task_id_validation_rejects_free_form_shell_text(self):
        for value in ("; rm -rf /", "$(id)", "../../task", "AK BER MET"):
            self.assertEqual(v2.task_details(value), "Invalid Task-ID.")
            self.assertEqual(v2.task_logs(value), "Invalid Task-ID.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
