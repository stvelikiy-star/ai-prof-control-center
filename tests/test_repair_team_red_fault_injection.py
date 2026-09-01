from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_team_red_fault_injection.py"
SPEC = importlib.util.spec_from_file_location("repair_team_red_fault_injection", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load Repair Team RED fault injection")
red = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = red
SPEC.loader.exec_module(red)


class RepairTeamRedFaultInjectionTests(unittest.TestCase):
    def test_red_fault_matrix_exercises_owner_terminal_pipeline(self):
        report = red.run_red_shadow(ROOT, git_status_fn=lambda: "stable-tree")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["version"], 1)
        self.assertEqual(report["scenario_id"], "FI_REPAIR_TEAM_RED_OWNER_TERMINAL_V1")
        self.assertEqual(report["project_id"], "ai-prof-control-center")
        self.assertEqual(report["probe_id"], "runtime-checkout")
        self.assertEqual(report["response_class"], "RED")
        self.assertEqual(
            report["transitions"],
            ["pending_failure", "opened", "updated", "pending_recovery", "resolved"],
        )
        self.assertTrue(report["correlation_evidence_validated"])
        self.assertTrue(report["diagnosis_protocol_validated"])
        self.assertTrue(report["diagnosis_drain_validated"])
        self.assertEqual(report["effective_next_action"], "OWNER_ACTION_REQUIRED")
        self.assertTrue(report["owner_terminal_created"])
        self.assertFalse(report["repair_task_created"])
        self.assertFalse(report["bridge_blocked"])
        self.assertFalse(report["green_authority_granted"])
        self.assertEqual(report["shadow_queue_health"], "healthy")
        self.assertTrue(report["stale_packet_archived"])
        self.assertFalse(report["external_ai_called"])
        self.assertFalse(report["production_queue_mutated"])
        self.assertFalse(report["target_repository_mutated"])
        self.assertFalse(report["real_state_mutated"])
        self.assertEqual(report["verification_level"], "shadow")

    def test_red_matrix_is_bound_to_current_registered_red_probe(self):
        response_class, project_path = red.base._validate_route(
            ROOT,
            red.DEFAULT_PROJECT_ID,
            red.DEFAULT_PROBE_ID,
        )
        self.assertEqual(response_class, "RED")
        self.assertEqual(red.DEFAULT_PROBE_ID, "runtime-checkout")
        self.assertEqual(
            project_path,
            Path("/home/agent/projects/ai-prof-control-center-maintenance"),
        )
        self.assertTrue((project_path / red.base.DEFAULT_EVIDENCE_SOURCE).is_file())

    def test_non_red_probe_fails_before_fault_injection(self):
        with self.assertRaisesRegex(red.FaultInjectionError, "requires RED policy"):
            red.run_red_shadow(
                ROOT,
                probe_id="maintenance-checkout",
                git_status_fn=lambda: "stable-tree",
            )

    def test_unknown_probe_fails_before_fault_injection(self):
        with self.assertRaisesRegex(red.FaultInjectionError, "not uniquely registered"):
            red.run_red_shadow(
                ROOT,
                probe_id="does-not-exist",
                git_status_fn=lambda: "stable-tree",
            )

    def test_red_matrix_reuses_audited_shadow_pipeline_helpers(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "base._reconcile_one",
            "base._generate_correlated_packets",
            "base.diagnosis_queue_drain.drain",
            "base.repair_task_bridge_drain.drain",
            "base.shadow_queue_health.build_snapshot",
            "base.shadow_queue_health.read_health",
            "OWNER_ACTION_REQUIRED",
        ):
            self.assertIn(required, source)

    def test_red_matrix_has_no_service_deploy_or_publication_mutation_commands(self):
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
        self.assertIn('"external_ai_called": False', source)
        self.assertIn('"repair_task_created": False', source)
        self.assertIn('"bridge_blocked": False', source)

    def test_existing_recovery_evidence_marker_is_untouched(self):
        existing = ROOT / "tests" / "test_repair_team_shadow_fault_injection.py"
        source = existing.read_text(encoding="utf-8")
        self.assertIn(
            "def test_shadow_fault_injection_exercises_real_incident_lifecycle",
            source,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
