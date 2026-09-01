from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))
LEGACY_PATH = ORCHESTRATOR / "telegram_bridge.py"
V2_PATH = ORCHESTRATOR / "telegram_bridge_v2.py"

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

    def test_help_exposes_diagnostics_repair_views_and_reasserts_safety(self):
        self.assertIn("/ai health", v2.V2_HELP)
        self.assertIn("/ai queue [project]", v2.V2_HELP)
        self.assertIn("/ai incidents [project]", v2.V2_HELP)
        self.assertIn("/ai critical", v2.V2_HELP)
        self.assertIn("/ai recovery [project]", v2.V2_HELP)
        self.assertIn("/ai repairs [project]", v2.V2_HELP)
        self.assertIn("/ai logs <TASK_ID>", v2.V2_HELP)
        self.assertIn("/ai blockers <project>", v2.V2_HELP)
        self.assertIn("/ai git <project> status", v2.V2_HELP)
        normalized_help = " ".join(v2.V2_HELP.lower().split())
        self.assertIn("no force-push", normalized_help)
        self.assertIn("no migration", normalized_help)
        self.assertIn("no restart", normalized_help)
        self.assertIn("no deploy", normalized_help)

    def test_unauthorized_update_is_silent(self):
        client = FakeClient()
        update = {"message": {"chat": {"id": -1001}, "from": {"id": 99}, "text": "/ai health"}}
        v2.extended_handle_update(update, self.config, client)
        self.assertEqual(client.messages, [])

    def test_unauthorized_repair_command_is_silent(self):
        client = FakeClient()
        update = {"message": {"chat": {"id": -1001}, "from": {"id": 99}, "text": "/ai incidents"}}
        with mock.patch.object(v2, "incidents_message") as renderer:
            v2.extended_handle_update(update, self.config, client)
        renderer.assert_not_called()
        self.assertEqual(client.messages, [])

    def test_health_command_is_owner_only_and_sent(self):
        client = FakeClient()
        update = {"message": {"chat": {"id": -1001}, "from": {"id": 42}, "text": "/ai health"}}
        with mock.patch.object(v2, "health_message", return_value="HEALTH PASS"):
            v2.extended_handle_update(update, self.config, client)
        self.assertEqual(client.messages, ["HEALTH PASS"])

    def test_owner_repair_command_is_dispatched(self):
        client = FakeClient()
        update = {
            "message": {
                "chat": {"id": -1001},
                "from": {"id": 42},
                "text": "/ai incidents demo",
            }
        }
        with mock.patch.object(v2, "_projects", return_value={"demo": {"path": "/tmp/demo"}}), \
             mock.patch.object(v2, "incidents_message", return_value="INCIDENTS") as renderer:
            v2.extended_handle_update(update, self.config, client)
        renderer.assert_called_once_with("demo")
        self.assertEqual(client.messages, ["INCIDENTS"])

    def test_unknown_repair_project_is_rejected_before_renderer(self):
        client = FakeClient()
        update = {
            "message": {
                "chat": {"id": -1001},
                "from": {"id": 42},
                "text": "/ai recovery unknown",
            }
        }
        with mock.patch.object(v2, "_projects", return_value={"demo": {"path": "/tmp/demo"}}), \
             mock.patch.object(v2, "recovery_message") as renderer:
            v2.extended_handle_update(update, self.config, client)
        renderer.assert_not_called()
        self.assertEqual(client.messages, ["Unknown project: unknown"])

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

    def test_incidents_filter_and_redaction(self):
        payload = {
            "open_incidents": [
                {
                    "incident_id": "INC-DEMO-ABCDEF1234",
                    "project_id": "demo",
                    "probe_id": "runtime",
                    "severity": "critical",
                    "failure_count": 2,
                    "updated_at": "2026-09-01T00:00:01+00:00",
                    "last_detail": "password=SUPERSECRET service failed",
                },
                {
                    "incident_id": "INC-OTHER-ABCDEF1235",
                    "project_id": "other",
                    "probe_id": "runtime",
                    "severity": "warning",
                    "failure_count": 1,
                    "updated_at": "2026-09-01T00:00:02+00:00",
                    "last_detail": "other failure",
                },
            ]
        }
        with mock.patch.object(v2, "incident_summary", return_value=payload):
            text = v2.incidents_message("demo")
            critical = v2.critical_message()
        self.assertIn("INC-DEMO-ABCDEF1234", text)
        self.assertNotIn("INC-OTHER-ABCDEF1235", text)
        self.assertNotIn("SUPERSECRET", text)
        self.assertIn("[REDACTED]", text)
        self.assertIn("INC-DEMO-ABCDEF1234", critical)
        self.assertNotIn("INC-OTHER-ABCDEF1235", critical)

    def test_recovery_view_exposes_state_not_authority_evidence_paths(self):
        contract = {
            "demo": {
                "project_id": "demo",
                "recovery_mode": "staged_activation",
                "verification_level": "shadow",
                "checkpoint_evidence": ["private/path.py:marker"],
                "rollback_evidence": ["private/path.py:rollback"],
                "restore_test_evidence": ["private/test.py:test"],
                "fault_injection_evidence": ["private/test.py:fault"],
                "production_ready": False,
            }
        }
        with mock.patch.object(v2.recovery_gate, "load_recovery_contracts", return_value=contract), \
             mock.patch.object(
                 v2.recovery_gate,
                 "recovery_readiness",
                 return_value=(False, ["RECOVERY_VERIFICATION_LEVEL_INSUFFICIENT"]),
             ):
            text = v2.recovery_message("demo")
        self.assertIn("mode=staged_activation", text)
        self.assertIn("verification=shadow", text)
        self.assertIn("production=BLOCKED", text)
        self.assertIn("RECOVERY_VERIFICATION_LEVEL_INSUFFICIENT", text)
        self.assertNotIn("private/path.py", text)
        self.assertNotIn("private/test.py", text)

    def test_repairs_pipeline_uses_incident_binding_for_project_filter(self):
        with tempfile.TemporaryDirectory(prefix="ai-prof-repair-view-") as tmp:
            state = Path(tmp)
            incident_id = "INC-DEMO-ABCDEF1234"
            other_incident_id = "INC-OTHER-ABCDEF1235"
            (state / "incidents/open").mkdir(parents=True)
            (state / "incidents/open/demo.json").write_text(
                json.dumps({"incident_id": incident_id, "project_id": "demo"}), encoding="utf-8"
            )
            (state / "incidents/open/other.json").write_text(
                json.dumps({"incident_id": other_incident_id, "project_id": "other"}), encoding="utf-8"
            )
            for relative in ("diagnosis/pending", "repair_bridge/tasks", "operations_bridge/tasks"):
                (state / relative).mkdir(parents=True)
            (state / "diagnosis/pending/demo.json").write_text(
                json.dumps({"incident_id": incident_id, "project_id": "demo"}), encoding="utf-8"
            )
            (state / "repair_bridge/tasks/demo.json").write_text(
                json.dumps({"incident_id": incident_id}), encoding="utf-8"
            )
            (state / "operations_bridge/tasks/other.json").write_text(
                json.dumps({"incident_id": other_incident_id}), encoding="utf-8"
            )
            locations = {
                "DEMO_20260812T100000Z_ABCDEF": [
                    ("pending", Path("demo.md"), {"Project-Path": "/tmp/demo"})
                ],
                "OTHER_20260812T100001Z_ABCDEF": [
                    ("failed", Path("other.md"), {"Project-Path": "/tmp/other"})
                ],
            }
            with mock.patch.object(v2, "STATE_ROOT", state), \
                 mock.patch.object(v2, "_projects", return_value={
                     "demo": {"path": "/tmp/demo"},
                     "other": {"path": "/tmp/other"},
                 }), \
                 mock.patch.object(legacy, "_queue_locations", return_value=locations), \
                 mock.patch.object(v2, "incident_summary", return_value={
                     "open_incidents": [{"incident_id": incident_id, "project_id": "demo"}]
                 }):
                text = v2.repairs_message("demo")
        self.assertIn("diagnosis.pending=1", text)
        self.assertIn("code.tasks=1", text)
        self.assertIn("operations.tasks=0", text)
        self.assertIn("pending=1", text)
        self.assertNotIn("failed=1", text)
        self.assertIn("Open incidents: 1", text)

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

    def test_repair_status_renderers_have_no_execution_boundary(self):
        source = "\n".join(
            inspect.getsource(fn)
            for fn in (
                v2.incidents_message,
                v2.critical_message,
                v2.recovery_message,
                v2.repairs_message,
                v2._read_state_record,
                v2._state_bucket_count,
            )
        )
        for forbidden in (
            "subprocess.run",
            "operations_runner",
            "release_flow",
            "systemctl",
            "docker",
            "supabase",
            "submit_task",
            "os.system",
        ):
            self.assertNotIn(forbidden, source)

    def test_v4_live_entrypoint_delegates_through_v3_to_v2(self):
        unit = (ROOT / "systemd/ai-prof-telegram-bridge.service").read_text(encoding="utf-8")
        v4_source = (ORCHESTRATOR / "telegram_bridge_v4.py").read_text(encoding="utf-8")
        v3_source = (ORCHESTRATOR / "telegram_bridge_v3.py").read_text(encoding="utf-8")
        self.assertIn("telegram_bridge_v4.py", unit)
        self.assertIn("import telegram_bridge_v3 as v3", v4_source)
        self.assertIn("return v3.main()", v4_source)
        self.assertIn("import telegram_bridge_v2 as v2", v3_source)
        self.assertIn("return v2.main()", v3_source)

    def test_task_id_validation_rejects_free_form_shell_text(self):
        for value in ("; rm -rf /", "$(id)", "../../task", "AK BER MET"):
            self.assertEqual(v2.task_details(value), "Invalid Task-ID.")
            self.assertEqual(v2.task_logs(value), "Invalid Task-ID.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
