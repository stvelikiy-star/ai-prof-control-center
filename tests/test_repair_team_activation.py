from __future__ import annotations

import datetime as dt
import importlib.util
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "activate_repair_team_v1.py"
SPEC = importlib.util.spec_from_file_location("activate_repair_team_v1", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load Repair Team activation")
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)


class RepairTeamActivationTests(unittest.TestCase):
    def test_scope_is_exactly_four_repair_units_and_two_phases(self):
        self.assertEqual(
            activation.REPAIR_UNITS,
            (
                "ai-prof-repair-monitor.service",
                "ai-prof-repair-monitor.timer",
                "ai-prof-repair-diagnosis.service",
                "ai-prof-repair-diagnosis.timer",
            ),
        )
        self.assertEqual(
            activation.PHASE_UNITS,
            {
                "monitor": (
                    "ai-prof-repair-monitor.service",
                    "ai-prof-repair-monitor.timer",
                ),
                "diagnosis": (
                    "ai-prof-repair-diagnosis.service",
                    "ai-prof-repair-diagnosis.timer",
                ),
            },
        )

    def test_activation_is_unit_only_and_requires_exact_main_identity(self):
        preflight = inspect.getsource(activation.verify_preconditions)
        self.assertIn('branch != "main"', preflight)
        self.assertIn("head != approved_sha", preflight)
        self.assertIn('git("fetch", "--prune", "origin", "main"', preflight)
        self.assertIn('git("rev-parse", "origin/main")', preflight)
        self.assertIn("remote_sha != approved_sha", preflight)
        execution_source = "\n".join(
            inspect.getsource(fn)
            for fn in (
                activation.run,
                activation.git,
                activation.sudo,
                activation.verify_repository_identity,
                activation.verify_preconditions,
                activation.create_checkpoint,
                activation.install_phase_units,
                activation.activate_phase_runtime,
                activation._restore_unit_files,
                activation._restore_unit_states,
                activation.rollback,
                activation.activate,
            )
        )
        for forbidden in (
            'git("switch"',
            'git("merge"',
            '"reset", "--hard"',
            '"clean", "-fd"',
            '"push", "--force"',
            "supabase db push",
            "npm run deploy",
        ):
            self.assertNotIn(forbidden, execution_source)

    def test_monitor_phase_rejects_preexisting_active_diagnosis_timer(self):
        states = {
            name: {"enabled": "disabled", "active": "inactive"}
            for name in activation.REPAIR_UNITS
        }
        states[activation.DIAGNOSIS_TIMER] = {"enabled": "enabled", "active": "active"}
        with mock.patch.object(activation.os, "geteuid", return_value=1000), \
             mock.patch.object(activation, "LIVE", Path("/tmp/live")), \
             mock.patch.object(activation, "STATE_ROOT", Path("/tmp/state")), \
             mock.patch.object(Path, "is_dir", return_value=True), \
             mock.patch.object(Path, "is_symlink", return_value=False), \
             mock.patch.object(activation.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(activation, "git", side_effect=["", "main", "a" * 40, None, "a" * 40, ""]), \
             mock.patch.object(activation, "verify_repository_identity"), \
             mock.patch.object(activation, "validate_staged_units"), \
             mock.patch.object(activation, "sudo"), \
             mock.patch.object(activation, "unit_snapshot", return_value=states):
            with self.assertRaisesRegex(activation.ActivationError, "diagnosis timer is already"):
                activation.verify_preconditions("a" * 40, "monitor")

    def test_diagnosis_phase_requires_monitor_and_zero_privileged_bindings(self):
        source = inspect.getsource(activation.verify_preconditions)
        self.assertIn("requires active enabled monitor timer", source)
        self.assertIn("verify_monitor_evidence()", source)
        self.assertIn("verify_zero_privileged_bindings()", source)

    def test_partial_install_failure_must_trigger_rollback(self):
        meta = {
            "branch": "main",
            "head": "a" * 40,
            "phase": "monitor",
            "unit_states": {
                name: {"enabled": "disabled", "active": "inactive"}
                for name in activation.REPAIR_UNITS
            },
        }
        checkpoint = Path("/tmp/repair-team-checkpoint")
        with mock.patch.object(activation, "verify_preconditions", return_value=meta), \
             mock.patch.object(activation, "git", return_value=""), \
             mock.patch.object(activation, "create_checkpoint", return_value=checkpoint), \
             mock.patch.object(
                 activation,
                 "install_phase_units",
                 side_effect=activation.ActivationError("second unit install failed"),
             ), \
             mock.patch.object(activation, "rollback") as rollback:
            with self.assertRaisesRegex(activation.ActivationError, "second unit install failed"):
                activation.activate("a" * 40, "monitor")
        rollback.assert_called_once_with(checkpoint)

    def test_runtime_activation_enables_only_phase_timer_and_starts_phase_service(self):
        calls: list[tuple] = []

        def fake_sudo(*args, **kwargs):
            calls.append(args)
            return None

        with mock.patch.object(activation, "sudo", side_effect=fake_sudo), \
             mock.patch.object(activation, "systemd_state", return_value="enabled") as state:
            state.side_effect = ["enabled", "active"]
            activation.activate_phase_runtime("monitor")
        self.assertIn(("systemctl", "enable", "--now", activation.MONITOR_TIMER), calls)
        self.assertIn(("systemctl", "start", activation.MONITOR_SERVICE), calls)
        flattened = " ".join(" ".join(call) for call in calls)
        self.assertNotIn(activation.DIAGNOSIS_TIMER, flattened)
        self.assertNotIn(activation.DIAGNOSIS_SERVICE, flattened)

    def test_monitor_evidence_requires_fresh_real_control_observation(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-evidence-") as tmp:
            state = Path(tmp)
            (state / "monitoring").mkdir()
            (state / "incidents").mkdir()
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            (state / "monitoring/latest.json").write_text(
                json.dumps({
                    "version": 1,
                    "generated_at": now,
                    "observations": [{"project_id": "ai-prof-control-center"}],
                }),
                encoding="utf-8",
            )
            (state / "incidents/summary.json").write_text(
                json.dumps({
                    "version": 1,
                    "generated_at": now,
                    "open_count": 0,
                    "resolved_count": 0,
                    "open_incidents": [],
                }),
                encoding="utf-8",
            )
            with mock.patch.object(activation, "STATE_ROOT", state):
                evidence = activation.verify_monitor_evidence()
            self.assertEqual(evidence["observations"], 1)
            self.assertEqual(evidence["open_incidents"], 0)

    def test_stale_monitor_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-stale-") as tmp:
            path = Path(tmp) / "evidence.json"
            stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
            path.write_text(json.dumps({"version": 1, "generated_at": stale}), encoding="utf-8")
            with self.assertRaisesRegex(activation.ActivationError, "stale"):
                activation._load_fresh_json(path, 1)

    def test_zero_privileged_binding_gate_requires_literal_empty_registry(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-bindings-") as tmp:
            live = Path(tmp)
            (live / "orchestrator").mkdir()
            path = live / "orchestrator/repair_operation_bindings.json"
            path.write_text(json.dumps({"version": 1, "bindings": []}), encoding="utf-8")
            with mock.patch.object(activation, "LIVE", live):
                activation.verify_zero_privileged_bindings()
            path.write_text(json.dumps({"version": 1, "bindings": [{"x": 1}]}), encoding="utf-8")
            with mock.patch.object(activation, "LIVE", live):
                with self.assertRaisesRegex(activation.ActivationError, "zero privileged bindings"):
                    activation.verify_zero_privileged_bindings()

    def test_checkpoint_records_git_bundle_all_four_units_and_unit_states(self):
        source = inspect.getsource(activation.create_checkpoint)
        self.assertIn('"bundle", "create"', source)
        self.assertIn("UNIT_STATES.json", source)
        self.assertIn("PREVIOUS_SHA", source)
        self.assertIn("PREVIOUS_BRANCH", source)
        self.assertIn("for name in REPAIR_UNITS", source)

    def test_activation_failure_path_contains_automatic_rollback(self):
        source = inspect.getsource(activation.activate)
        self.assertIn("rollback(backup)", source)
        self.assertIn("automatic rollback ALSO failed", source)

    def test_staged_units_have_state_only_write_boundary_and_no_privileged_runner(self):
        for name in (activation.MONITOR_SERVICE, activation.DIAGNOSIS_SERVICE):
            text = (ROOT / "systemd" / name).read_text(encoding="utf-8")
            rw = [line for line in text.splitlines() if line.startswith("ReadWritePaths=")]
            self.assertEqual(rw, ["ReadWritePaths=/home/agent/.local/state/ai-prof-control-center"])
            for forbidden in (
                "operations_runner.py",
                "repair_operations_bridge.py",
                "release_flow.py",
                "supabase db push",
                "docker restart",
                "git push",
            ):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
