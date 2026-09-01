from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_team_shadow_readiness.py"
SPEC = importlib.util.spec_from_file_location("repair_team_shadow_readiness", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load Repair Team shadow readiness gate")
readiness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = readiness
SPEC.loader.exec_module(readiness)


class RepairTeamShadowReadinessTests(unittest.TestCase):
    def _write_static_authority(
        self,
        root: Path,
        *,
        allow_merge: bool = False,
        allow_deploy: bool = False,
        bindings: list | None = None,
        control_policies: dict | None = None,
    ) -> None:
        orchestrator = root / "orchestrator"
        orchestrator.mkdir(parents=True, exist_ok=True)
        (orchestrator / "config.json").write_text(
            json.dumps({
                "allow_merge": allow_merge,
                "allow_production_deploy": allow_deploy,
                "require_codex_pass": True,
            }),
            encoding="utf-8",
        )
        (orchestrator / "repair_operation_bindings.json").write_text(
            json.dumps({"version": 1, "bindings": bindings or []}),
            encoding="utf-8",
        )
        (orchestrator / "repair_policies.json").write_text(
            json.dumps({
                "version": 1,
                "projects": {
                    "ai-prof-control-center": control_policies
                    or dict(readiness.EXPECTED_CONTROL_POLICIES),
                    "demo": {"runtime": "RED"},
                },
            }),
            encoding="utf-8",
        )

    def test_full_readiness_report_passes_current_shadow_stack(self):
        report = readiness.build_readiness_report(ROOT)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            report["readiness"],
            "CODE_READY_FOR_REVIEWED_SHADOW_ACTIVATION",
        )
        self.assertEqual(report["project_id"], "ai-prof-control-center")
        self.assertFalse(report["production_ready"])
        self.assertFalse(report["live_runtime_verified"])
        self.assertFalse(report["live_activation_performed"])
        self.assertFalse(report["authority"]["allow_merge"])
        self.assertFalse(report["authority"]["allow_production_deploy"])
        self.assertEqual(report["authority"]["privileged_bindings"], 0)
        self.assertFalse(report["authority"]["green_authority"])
        self.assertEqual(report["recovery"]["verification_level"], "shadow")
        self.assertFalse(report["recovery"]["production_ready"])
        self.assertEqual(report["activation"]["activation_contract"], "V2")
        self.assertFalse(report["activation"]["live_activation_performed"])
        self.assertEqual(report["fault_matrix"]["yellow"]["response_class"], "YELLOW")
        self.assertEqual(
            report["fault_matrix"]["yellow"]["effective_next_action"],
            "PREPARE_REPAIR_FOR_OWNER_REVIEW",
        )
        self.assertEqual(report["fault_matrix"]["red"]["response_class"], "RED")
        self.assertEqual(
            report["fault_matrix"]["red"]["effective_next_action"],
            "OWNER_ACTION_REQUIRED",
        )
        self.assertTrue(report["fault_matrix"]["red"]["owner_terminal_created"])
        self.assertFalse(report["fault_matrix"]["external_ai_called"])
        self.assertFalse(report["fault_matrix"]["real_state_mutated"])

    def test_static_authority_fails_closed_on_merge_deploy_or_binding(self):
        with tempfile.TemporaryDirectory(prefix="repair-readiness-authority-") as tmp:
            root = Path(tmp)
            self._write_static_authority(root, allow_merge=True)
            with self.assertRaisesRegex(readiness.ShadowReadinessError, "allow_merge=false"):
                readiness.verify_static_authority(root)

            self._write_static_authority(root, allow_deploy=True)
            with self.assertRaisesRegex(readiness.ShadowReadinessError, "allow_production_deploy=false"):
                readiness.verify_static_authority(root)

            self._write_static_authority(root, bindings=[{"operation": "restart"}])
            with self.assertRaisesRegex(readiness.ShadowReadinessError, "zero privileged"):
                readiness.verify_static_authority(root)

    def test_static_authority_rejects_green_or_control_policy_drift(self):
        with tempfile.TemporaryDirectory(prefix="repair-readiness-policy-") as tmp:
            root = Path(tmp)
            policies = dict(readiness.EXPECTED_CONTROL_POLICIES)
            policies["maintenance-checkout"] = "GREEN"
            self._write_static_authority(root, control_policies=policies)
            with self.assertRaisesRegex(readiness.ShadowReadinessError, "policy drifted"):
                readiness.verify_static_authority(root)

            self._write_static_authority(root)
            path = root / "orchestrator" / "repair_policies.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["projects"]["demo"]["runtime"] = "GREEN"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(readiness.ShadowReadinessError, "GREEN authority"):
                readiness.verify_static_authority(root)

    def test_recovery_contract_is_valid_but_explicitly_not_production_ready(self):
        recovery = readiness.verify_shadow_recovery_contract(ROOT)
        self.assertEqual(recovery["recovery_mode"], "staged_activation")
        self.assertEqual(recovery["verification_level"], "shadow")
        self.assertFalse(recovery["production_ready"])
        self.assertIn("PRODUCTION_RECOVERY_NOT_APPROVED", recovery["production_blockers"])
        self.assertGreaterEqual(recovery["fault_evidence_count"], 1)

    def test_activation_v2_contract_matches_current_repository_units(self):
        result = readiness.verify_activation_v2_contract(ROOT)
        self.assertEqual(result["activation_contract"], "V2")
        self.assertEqual(
            result["monitor_runners"],
            ["incident_engine_shadow_health_canary.py", "diagnosis_packet_canary.py"],
        )
        self.assertEqual(
            result["diagnosis_runners"],
            ["diagnosis_queue_drain.py", "repair_task_bridge_drain.py", "shadow_queue_health.py"],
        )
        self.assertFalse(result["live_activation_performed"])

    def test_fault_report_validators_fail_closed_on_authority_or_health_drift(self):
        yellow = {
            "status": "PASS",
            "response_class": "YELLOW",
            "effective_next_action": "PREPARE_REPAIR_FOR_OWNER_REVIEW",
            "green_authority_granted": False,
            "external_ai_called": False,
            "production_queue_mutated": False,
            "target_repository_mutated": False,
            "real_state_mutated": False,
            "shadow_queue_health": "healthy",
        }
        red = {
            "status": "PASS",
            "response_class": "RED",
            "effective_next_action": "OWNER_ACTION_REQUIRED",
            "owner_terminal_created": True,
            "repair_task_created": False,
            "bridge_blocked": False,
            "green_authority_granted": False,
            "external_ai_called": False,
            "production_queue_mutated": False,
            "target_repository_mutated": False,
            "real_state_mutated": False,
            "shadow_queue_health": "healthy",
        }
        readiness._validate_yellow_report(dict(yellow))
        readiness._validate_red_report(dict(red))

        yellow["green_authority_granted"] = True
        with self.assertRaisesRegex(readiness.ShadowReadinessError, "violated isolation"):
            readiness._validate_yellow_report(yellow)

        red["bridge_blocked"] = True
        with self.assertRaisesRegex(readiness.ShadowReadinessError, "violated terminal"):
            readiness._validate_red_report(red)

    def test_gate_does_not_call_activation_or_production_mutation_surfaces(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "activation.activate(",
            "systemctl start",
            "systemctl enable",
            "docker restart",
            "supabase db push",
            "npm run deploy",
            "git merge",
            "git push",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"production_ready": False', source)
        self.assertIn('"live_runtime_verified": False', source)
        self.assertIn('"live_activation_performed": False', source)

    def test_fault_matrix_failure_blocks_readiness_report(self):
        with mock.patch.object(
            readiness,
            "verify_fault_matrix",
            side_effect=readiness.ShadowReadinessError("fault matrix failed"),
        ):
            with self.assertRaisesRegex(readiness.ShadowReadinessError, "fault matrix failed"):
                readiness.build_readiness_report(ROOT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
