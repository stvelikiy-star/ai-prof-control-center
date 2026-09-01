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
SCRIPT = ROOT / "scripts" / "activate_repair_team_v2.py"
SPEC = importlib.util.spec_from_file_location("activate_repair_team_v2", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load Repair Team activation V2")
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)


class RepairTeamActivationV2Tests(unittest.TestCase):
    def _healthy_payload(self, timestamp: str | None = None) -> dict:
        return {
            "version": 1,
            "timestamp": timestamp or dt.datetime.now(dt.timezone.utc).isoformat(),
            "ok": True,
            "state": "healthy",
            "thresholds": {
                "max_pending_age_seconds": 1800.0,
                "max_health_age_seconds": 1500.0,
            },
            "diagnosis": {
                "pending_count": 1,
                "oldest_pending_age_seconds": 10.0,
                "blocked_open_count": 0,
            },
            "bridge": {
                "unprocessed_count": 0,
                "oldest_unprocessed_age_seconds": None,
                "blocked_open_count": 0,
            },
            "reasons": [],
        }

    def _write_health(self, state: Path, payload: dict) -> Path:
        path = state / activation.SHADOW_HEALTH_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_current_repository_units_match_exact_v2_runner_contract(self):
        with mock.patch.object(
            activation.base,
            "_unit_source",
            side_effect=lambda name: ROOT / "systemd" / name,
        ):
            activation.validate_staged_units()
        self.assertEqual(
            activation.MONITOR_RUNNERS,
            ("incident_engine_shadow_health_canary.py", "diagnosis_packet_canary.py"),
        )
        self.assertEqual(
            activation.DIAGNOSIS_RUNNERS,
            ("diagnosis_queue_drain.py", "repair_task_bridge_drain.py", "shadow_queue_health.py"),
        )

    def test_old_monitor_runner_contract_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-v2-units-") as tmp:
            unit_root = Path(tmp)
            for name in activation.base.REPAIR_UNITS:
                source = ROOT / "systemd" / name
                text = source.read_text(encoding="utf-8")
                if name == activation.base.MONITOR_SERVICE:
                    text = text.replace(
                        "incident_engine_shadow_health_canary.py",
                        "incident_engine.py",
                    )
                (unit_root / name).write_text(text, encoding="utf-8")

            def source_for(name: str) -> Path:
                return unit_root / name

            with mock.patch.object(activation.base, "_unit_source", side_effect=source_for):
                with self.assertRaisesRegex(activation.ActivationError, "monitor unit runner contract mismatch"):
                    activation.validate_staged_units()

    def test_extra_or_privileged_execstart_is_rejected_by_exact_contract(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-v2-extra-") as tmp:
            unit_root = Path(tmp)
            for name in activation.base.REPAIR_UNITS:
                text = (ROOT / "systemd" / name).read_text(encoding="utf-8")
                if name == activation.base.DIAGNOSIS_SERVICE:
                    text += "\nExecStart=/usr/bin/python3 /home/agent/projects/ai-prof-control-center/orchestrator/operations_runner.py\n"
                (unit_root / name).write_text(text, encoding="utf-8")
            with mock.patch.object(
                activation.base,
                "_unit_source",
                side_effect=lambda name: unit_root / name,
            ):
                with self.assertRaisesRegex(activation.ActivationError, "diagnosis unit runner contract mismatch"):
                    activation.validate_staged_units()

    def test_fresh_healthy_shadow_queue_health_is_accepted(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-v2-health-") as tmp:
            state = Path(tmp)
            self._write_health(state, self._healthy_payload())
            with mock.patch.object(activation.base, "STATE_ROOT", state):
                evidence = activation.verify_shadow_queue_health_evidence()
        self.assertEqual(evidence["state"], "healthy")
        self.assertEqual(evidence["diagnosis_pending"], 1)
        self.assertEqual(evidence["diagnosis_blocked_open"], 0)

    def test_degraded_shadow_queue_health_blocks_activation(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-v2-degraded-") as tmp:
            state = Path(tmp)
            payload = self._healthy_payload()
            payload["ok"] = False
            payload["state"] = "degraded"
            payload["reasons"] = ["open_diagnosis_blocked"]
            self._write_health(state, payload)
            with mock.patch.object(activation.base, "STATE_ROOT", state):
                with self.assertRaisesRegex(activation.ActivationError, "degraded"):
                    activation.verify_shadow_queue_health_evidence()

    def test_stale_shadow_queue_health_blocks_activation(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-v2-stale-") as tmp:
            state = Path(tmp)
            stale = (
                dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(seconds=activation.base.FRESH_EVIDENCE_SECONDS + 30)
            ).isoformat()
            self._write_health(state, self._healthy_payload(stale))
            with mock.patch.object(activation.base, "STATE_ROOT", state):
                with self.assertRaisesRegex(activation.ActivationError, "stale"):
                    activation.verify_shadow_queue_health_evidence()

    def test_malformed_or_symlink_shadow_health_blocks_activation(self):
        with tempfile.TemporaryDirectory(prefix="repair-activation-v2-invalid-") as tmp:
            state = Path(tmp)
            path = self._write_health(state, self._healthy_payload())
            path.write_text("{bad json", encoding="utf-8")
            with mock.patch.object(activation.base, "STATE_ROOT", state):
                with self.assertRaisesRegex(activation.ActivationError, "invalid shadow queue health"):
                    activation.verify_shadow_queue_health_evidence()

            path.unlink()
            outside = state / "outside.json"
            outside.write_text(json.dumps(self._healthy_payload()), encoding="utf-8")
            path.symlink_to(outside)
            with mock.patch.object(activation.base, "STATE_ROOT", state):
                with self.assertRaisesRegex(activation.ActivationError, "missing or unsafe"):
                    activation.verify_shadow_queue_health_evidence()

    def test_diagnosis_post_activation_requires_monitor_bindings_and_shadow_health(self):
        approved = "a" * 40
        monitor_evidence = {"observations": 5, "open_incidents": 0, "resolved_incidents": 2}
        shadow_evidence = {"state": "healthy", "diagnosis_pending": 0}
        with mock.patch.object(
            activation.base,
            "git",
            side_effect=[approved, "main", ""],
        ), mock.patch.object(
            activation.base,
            "verify_monitor_evidence",
            return_value=monitor_evidence,
        ) as monitor, mock.patch.object(
            activation.base,
            "verify_zero_privileged_bindings",
        ) as bindings, mock.patch.object(
            activation,
            "verify_shadow_queue_health_evidence",
            return_value=shadow_evidence,
        ) as shadow, mock.patch.object(
            activation.base,
            "systemd_state",
            side_effect=["active", "active"],
        ):
            evidence = activation.verify_post_activation(approved, "diagnosis", "")
        monitor.assert_called_once_with()
        bindings.assert_called_once_with()
        shadow.assert_called_once_with()
        self.assertEqual(evidence["monitor_timer"], "active")
        self.assertEqual(evidence["diagnosis_timer"], "active")
        self.assertEqual(evidence["shadow_queue_health"], shadow_evidence)

    def test_monitor_post_activation_does_not_require_shadow_health(self):
        approved = "b" * 40
        expected = {"observations": 3, "open_incidents": 1, "resolved_incidents": 0}
        with mock.patch.object(
            activation.base,
            "git",
            side_effect=[approved, "main", ""],
        ), mock.patch.object(
            activation.base,
            "verify_monitor_evidence",
            return_value=expected,
        ) as monitor, mock.patch.object(
            activation,
            "verify_shadow_queue_health_evidence",
        ) as shadow:
            evidence = activation.verify_post_activation(approved, "monitor", "")
        monitor.assert_called_once_with()
        shadow.assert_not_called()
        self.assertEqual(evidence, expected)

    def test_v2_delegates_to_v1_activation_and_restores_overrides(self):
        original_validate = activation.base.validate_staged_units
        original_post = activation.base.verify_post_activation

        def fake_activate(approved_sha: str, phase: str):
            self.assertIs(activation.base.validate_staged_units, activation.validate_staged_units)
            self.assertIs(activation.base.verify_post_activation, activation.verify_post_activation)
            self.assertEqual(approved_sha, "c" * 40)
            self.assertEqual(phase, "diagnosis")
            return Path("/checkpoint"), {"ok": True}

        with mock.patch.object(activation.base, "activate", side_effect=fake_activate):
            result = activation.activate("c" * 40, "diagnosis")
        self.assertEqual(result[0], Path("/checkpoint"))
        self.assertIs(activation.base.validate_staged_units, original_validate)
        self.assertIs(activation.base.verify_post_activation, original_post)

    def test_v2_restores_overrides_even_when_v1_activation_fails(self):
        original_validate = activation.base.validate_staged_units
        original_post = activation.base.verify_post_activation
        with mock.patch.object(
            activation.base,
            "activate",
            side_effect=activation.ActivationError("canary failed"),
        ):
            with self.assertRaisesRegex(activation.ActivationError, "canary failed"):
                activation.activate("d" * 40, "monitor")
        self.assertIs(activation.base.validate_staged_units, original_validate)
        self.assertIs(activation.base.verify_post_activation, original_post)

    def test_v2_contains_no_second_checkpoint_or_rollback_implementation(self):
        source = inspect.getsource(activation)
        self.assertIn("return base.activate(approved_sha, phase)", source)
        for forbidden in (
            "def rollback(",
            "def create_checkpoint(",
            "systemctl enable",
            "git switch",
            "git merge",
            "supabase db push",
            "npm run deploy",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
