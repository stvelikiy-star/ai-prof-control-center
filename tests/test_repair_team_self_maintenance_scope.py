from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "orchestrator" / "projects.json"


class RepairTeamSelfMaintenanceScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(PROJECTS.read_text(encoding="utf-8"))
        cls.project = next(
            item for item in payload["projects"]
            if item["project_id"] == "ai-prof-control-center"
        )

    def test_observation_engines_are_repairable_in_maintenance_checkout(self):
        allowed = set(self.project["allowed_scope"])
        expected = {
            "orchestrator/monitoring_engine.py",
            "orchestrator/monitoring_profiles.py",
            "orchestrator/incident_engine.py",
            "tests/test_monitoring_engine.py",
            "tests/test_monitoring_profiles.py",
            "tests/test_incident_engine.py",
        }
        self.assertTrue(expected.issubset(allowed))

    def test_all_repair_authority_and_activation_files_remain_owner_gated(self):
        allowed = set(self.project["allowed_scope"])
        forbidden = set(self.project["forbidden_scope"])
        authority = {
            "orchestrator/monitoring_profiles.json",
            "orchestrator/repair_policies.json",
            "orchestrator/repair_policy.py",
            "orchestrator/repair_runbooks.json",
            "orchestrator/runbook_registry.py",
            "orchestrator/diagnosis_packet.py",
            "orchestrator/incident_diagnosis_runner.py",
            "orchestrator/project_test_contracts.json",
            "orchestrator/project_test_contracts.py",
            "orchestrator/project_recovery_contracts.json",
            "orchestrator/project_recovery_gate.py",
            "orchestrator/repair_operation_bindings.json",
            "orchestrator/repair_operation_bindings.py",
            "orchestrator/repair_operations_bridge.py",
            "orchestrator/repair_task_bridge.py",
            "scripts/activate_repair_team_v1.py",
            "scripts/repair_team_shadow_fault_injection.py",
            "systemd/**",
        }
        self.assertTrue(authority.issubset(forbidden))
        self.assertTrue(authority.isdisjoint(allowed))

    def test_authority_is_double_locked_by_explicit_allow_and_forbid_lists(self):
        allowed = set(self.project["allowed_scope"])
        forbidden = set(self.project["forbidden_scope"])
        overlap = allowed.intersection(forbidden)
        self.assertEqual(overlap, set())
        for path in forbidden:
            if path.startswith("orchestrator/repair_") or path.startswith("orchestrator/project_"):
                self.assertNotIn(path, allowed)

    def test_self_maintenance_still_cannot_push_merge_or_deploy(self):
        self.assertIs(self.project["allow_commits"], True)
        self.assertIs(self.project["allow_push"], False)
        self.assertIs(self.project["allow_merge"], False)
        self.assertIs(self.project["allow_deployment"], False)


if __name__ == "__main__":
    unittest.main()
