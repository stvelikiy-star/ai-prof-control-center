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
SCRIPT = ROOT / "scripts" / "activate_mobile_control_v2.py"
SPEC = importlib.util.spec_from_file_location("activate_mobile_control_v2_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load activation v2")
v2 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = v2
SPEC.loader.exec_module(v2)


class ActivateMobileControlV2Tests(unittest.TestCase):
    def test_public_control_center_identity_is_exact(self):
        expected = v2.expected_identity()
        result = subprocess.CompletedProcess([], 0, expected + "\n", "")
        with mock.patch.object(v2.v1, "run", return_value=result) as run:
            v2.verify_repository_identity()
        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], ["gh", "api", "repos/stvelikiy-star/ai-prof-control-center"])
        self.assertIn(".full_name", argv[-1])
        self.assertIn(".private", argv[-1])
        self.assertIn(".visibility", argv[-1])
        self.assertTrue(expected.endswith("\tfalse\tpublic"))

    def test_private_or_wrong_repository_identity_blocks(self):
        bad = "stvelikiy-star/ai-prof-control-center\tstvelikiy-star\tmain\ttrue\tprivate\n"
        result = subprocess.CompletedProcess([], 0, bad, "")
        with mock.patch.object(v2.v1, "run", return_value=result):
            with self.assertRaisesRegex(v2.v1.ActivationError, "identity mismatch"):
                v2.verify_repository_identity()

    def test_legacy_task_must_exist_exactly_once_and_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            pending = state / "queue" / "pending"
            pending.mkdir(parents=True)
            task = pending / f"{v2.CANONICAL_LEGACY_KOL_TASK}.md"
            task.write_text("Task-ID: legacy\n", encoding="utf-8")
            with mock.patch.object(v2, "STATE_ROOT", state):
                self.assertEqual(v2.legacy_kol_task_queue(), "pending")
                active = state / "queue" / "active"
                active.mkdir(parents=True)
                (active / task.name).write_text("duplicate\n", encoding="utf-8")
                with self.assertRaisesRegex(v2.v1.ActivationError, "exactly one queue"):
                    v2.legacy_kol_task_queue()

    def test_effective_control_unit_accepts_canonical_runtime(self):
        canonical = str(v2.v1.LIVE)
        values = {
            "WorkingDirectory": canonical,
            "ExecStart": (
                "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
                f"{canonical}/orchestrator/control_loop_service_night.py --daemon ; }}"
            ),
            "DropInPaths": "/etc/systemd/system/ai-prof-control-center.service.d/runtime.conf",
        }
        with mock.patch.object(v2, "systemd_property", side_effect=lambda name: values[name]):
            identity = v2.verify_effective_control_unit()
        self.assertEqual(identity["working_directory"], canonical)
        self.assertIn("control_loop_service_night.py", identity["exec_start"])

    def test_effective_control_unit_rejects_stale_canary_redirect(self):
        stale = "/home/agent/projects/ai-prof-control-center-night-watch"
        values = {
            "WorkingDirectory": stale,
            "ExecStart": (
                "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
                f"{stale}/orchestrator/control_loop_service_night.py "
                f"--root {stale} --daemon ; }}"
            ),
            "DropInPaths": "/etc/systemd/system/ai-prof-control-center.service.d/90-night-watch-canary.conf",
        }
        with mock.patch.object(v2, "systemd_property", side_effect=lambda name: values[name]):
            with self.assertRaisesRegex(v2.v1.ActivationError, "WorkingDirectory mismatch"):
                v2.verify_effective_control_unit()

    def test_effective_control_unit_rejects_noncanonical_root_override(self):
        canonical = str(v2.v1.LIVE)
        values = {
            "WorkingDirectory": canonical,
            "ExecStart": (
                "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
                f"{canonical}/orchestrator/control_loop_service_night.py "
                "--root /tmp/other-control-center --daemon ; }"
            ),
            "DropInPaths": "/etc/systemd/system/ai-prof-control-center.service.d/override.conf",
        }
        with mock.patch.object(v2, "systemd_property", side_effect=lambda name: values[name]):
            with self.assertRaisesRegex(v2.v1.ActivationError, "overrides --root"):
                v2.verify_effective_control_unit()

    def test_running_process_requires_systemd_heartbeat_and_proc_identity_match(self):
        canonical = v2.v1.LIVE
        cmdline = (
            f"/usr/bin/python3 {canonical}/orchestrator/"
            "control_loop_service_night.py --daemon"
        )
        with mock.patch.object(v2, "systemd_property", return_value="1234"), \
             mock.patch.object(v2, "read_process_identity", return_value=(cmdline, canonical)):
            result = v2.verify_running_control_process({"state": "paused", "pid": 1234})
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(result["cwd"], str(canonical))

    def test_running_process_rejects_heartbeat_pid_mismatch(self):
        with mock.patch.object(v2, "systemd_property", return_value="1234"):
            with self.assertRaisesRegex(v2.v1.ActivationError, "heartbeat PID"):
                v2.verify_running_control_process({"state": "paused", "pid": 9999})

    def test_running_process_rejects_stale_cmdline_even_with_matching_pid(self):
        stale = Path("/home/agent/projects/ai-prof-control-center-night-watch")
        cmdline = (
            f"/usr/bin/python3 {stale}/orchestrator/"
            "control_loop_service_night.py --daemon"
        )
        with mock.patch.object(v2, "systemd_property", return_value="1234"), \
             mock.patch.object(v2, "read_process_identity", return_value=(cmdline, v2.v1.LIVE)):
            with self.assertRaisesRegex(v2.v1.ActivationError, "canonical runtime script"):
                v2.verify_running_control_process({"state": "paused", "pid": 1234})

    def test_paused_runtime_requires_pause_pending_task_and_paused_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            run_dir = state / "run"
            pending = state / "queue" / "pending"
            run_dir.mkdir(parents=True)
            pending.mkdir(parents=True)
            pause = run_dir / "paused"
            pause.write_text("paused\n", encoding="utf-8")
            (pending / f"{v2.CANONICAL_LEGACY_KOL_TASK}.md").write_text(
                "Task-ID: legacy\n", encoding="utf-8"
            )
            (run_dir / "heartbeat.json").write_text(
                json.dumps({"state": "paused", "pid": 1234}) + "\n", encoding="utf-8"
            )
            with mock.patch.object(v2, "STATE_ROOT", state), \
                 mock.patch.object(v2, "PAUSE_FILE", pause), \
                 mock.patch.object(v2.v1, "service_active", return_value=True), \
                 mock.patch.object(v2, "verify_effective_control_unit", return_value={}), \
                 mock.patch.object(v2, "verify_running_control_process", return_value={}):
                heartbeat = v2.verify_paused_runtime()
            self.assertEqual(heartbeat["state"], "paused")

    def test_missing_pause_blocks_before_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            pending = state / "queue" / "pending"
            pending.mkdir(parents=True)
            (pending / f"{v2.CANONICAL_LEGACY_KOL_TASK}.md").write_text(
                "Task-ID: legacy\n", encoding="utf-8"
            )
            missing = state / "run" / "paused"
            with mock.patch.object(v2, "STATE_ROOT", state), \
                 mock.patch.object(v2, "PAUSE_FILE", missing), \
                 mock.patch.object(v2.os, "geteuid", return_value=1000), \
                 mock.patch.object(v2.v1, "LIVE", Path(tmp)), \
                 mock.patch.object(v2.shutil, "which", return_value="/usr/bin/tool"):
                (Path(tmp) / ".git").mkdir()
                with self.assertRaisesRegex(v2.v1.ActivationError, "pause guard"):
                    v2.verify_preconditions("1" * 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
