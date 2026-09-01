from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_team_shadow_fault_injection.py"
SPEC = importlib.util.spec_from_file_location("repair_team_shadow_fault_injection", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load Repair Team shadow fault injection")
fault = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fault
SPEC.loader.exec_module(fault)


class RepairTeamShadowFaultInjectionTests(unittest.TestCase):
    def test_shadow_fault_injection_exercises_real_incident_lifecycle(self):
        report = fault.run_shadow(ROOT, git_status_fn=lambda: "stable-tree")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["version"], 2)
        self.assertEqual(report["scenario_id"], "FI_REPAIR_TEAM_SHADOW_PIPELINE_V2")
        self.assertEqual(report["project_id"], "ai-prof-control-center")
        self.assertEqual(report["probe_id"], "maintenance-checkout")
        self.assertEqual(report["response_class"], "YELLOW")
        self.assertEqual(
            report["transitions"],
            ["pending_failure", "opened", "updated", "pending_recovery", "resolved"],
        )
        self.assertTrue(report["duplicate_suppressed"])
        self.assertTrue(report["correlation_evidence_validated"])
        self.assertTrue(report["diagnosis_protocol_validated"])
        self.assertTrue(report["diagnosis_drain_validated"])
        self.assertEqual(
            report["effective_next_action"],
            "PREPARE_REPAIR_FOR_OWNER_REVIEW",
        )
        self.assertFalse(report["green_authority_granted"])
        self.assertTrue(report["shadow_repair_task_created"])
        self.assertTrue(report["shadow_task_id"].startswith("REPAIR_AI_PROF_CONTROL_CENTER_"))
        self.assertTrue(report["bridge_drain_validated"])
        self.assertTrue(report["stale_packet_archived"])
        self.assertEqual(report["shadow_queue_health"], "healthy")
        self.assertFalse(report["external_ai_called"])
        self.assertFalse(report["production_queue_mutated"])
        self.assertFalse(report["target_repository_mutated"])
        self.assertFalse(report["real_state_mutated"])
        self.assertEqual(report["verification_level"], "shadow")

    def test_repository_mutation_is_detected(self):
        states = iter(["clean", "changed"])
        with self.assertRaisesRegex(fault.FaultInjectionError, "working tree changed"):
            fault.run_shadow(ROOT, git_status_fn=lambda: next(states))

    def test_unknown_probe_fails_before_injection(self):
        with self.assertRaisesRegex(fault.FaultInjectionError, "not uniquely registered"):
            fault.run_shadow(
                ROOT,
                probe_id="does-not-exist",
                git_status_fn=lambda: "stable-tree",
            )

    def test_v2_is_bound_to_existing_yellow_checkout_probe_and_safe_evidence(self):
        self.assertEqual(fault.DEFAULT_PROJECT_ID, "ai-prof-control-center")
        self.assertEqual(fault.DEFAULT_PROBE_ID, "maintenance-checkout")
        self.assertEqual(fault.DEFAULT_EVIDENCE_SOURCE, "orchestrator/telegram_bridge.py")
        self.assertEqual(
            fault.classify(ROOT, fault.DEFAULT_PROJECT_ID, fault.DEFAULT_PROBE_ID),
            "YELLOW",
        )

    def test_shadow_script_uses_current_pipeline_modules(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "incident_engine_canary.reconcile",
            "diagnosis_packet_canary.build_packet",
            "diagnosis_queue_drain.drain",
            "repair_task_bridge_drain.drain",
            "shadow_queue_health.build_snapshot",
            "shadow_queue_health.read_health",
        ):
            self.assertIn(required, source)

    def test_shadow_script_has_no_service_deploy_or_publication_mutation_commands(self):
        source = SCRIPT.read_text(encoding="utf-8")
        forbidden = (
            "systemctl restart",
            "systemctl stop",
            "systemctl enable",
            "docker restart",
            "docker compose up",
            "supabase db push",
            "npm run deploy",
            "git push",
            "git merge",
            "git reset --hard",
        )
        for value in forbidden:
            self.assertNotIn(value, source)

    def test_fake_codex_transport_cannot_be_mistaken_for_external_ai(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("invoke_fn=_fake_codex_invoker", source)
        self.assertIn('"external_ai_called": False', source)
        self.assertNotIn("cr.invoke_codex(", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
