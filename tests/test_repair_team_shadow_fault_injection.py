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
        self.assertEqual(report["scenario_id"], "FI_REPAIR_TEAM_INCIDENT_LIFECYCLE_V1")
        self.assertEqual(report["project_id"], "ai-prof-control-center")
        self.assertEqual(report["probe_id"], "maintenance-checkout")
        self.assertEqual(report["response_class"], "YELLOW")
        self.assertEqual(report["transitions"], ["opened", "updated", "resolved"])
        self.assertTrue(report["duplicate_suppressed"])
        self.assertTrue(report["diagnosis_packet_created"])
        self.assertTrue(report["stale_packet_archived"])
        self.assertFalse(report["execution_queue_created"])
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

    def test_shadow_script_has_no_service_or_deploy_mutation_commands(self):
        source = SCRIPT.read_text(encoding="utf-8")
        forbidden = (
            "systemctl restart",
            "systemctl stop",
            "docker restart",
            "docker compose up",
            "supabase db push",
            "npm run deploy",
            "git push",
        )
        for value in forbidden:
            self.assertNotIn(value, source)


if __name__ == "__main__":
    unittest.main()
