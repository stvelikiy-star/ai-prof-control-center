from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import repair_operation_bindings as bindings


class RepairOperationBindingTests(unittest.TestCase):
    def _root(self, rows: list[dict]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "orchestrator").mkdir()
        (root / "orchestrator" / "projects.json").write_text(
            json.dumps({"version": 1, "projects": [{
                "project_id": "ai-prof-control-center",
                "path": "/home/agent/projects/ai-prof-control-center-maintenance",
                "base_branch": "maintenance/base",
                "allowed_base_branches": ["maintenance/base"],
                "enabled": True,
            }]}), encoding="utf-8"
        )
        (root / "orchestrator" / "monitoring_profiles.json").write_text(
            json.dumps({"version": 1, "projects": {"ai-prof-control-center": {
                "enabled": True,
                "probes": [{
                    "id": "runtime",
                    "kind": "path_exists",
                    "path": "/home/agent/projects/ai-prof-control-center-maintenance",
                }],
            }}}), encoding="utf-8"
        )
        (root / "orchestrator" / "repair_runbooks.json").write_text(
            json.dumps({"version": 1, "runbooks": [{
                "runbook_id": "AI_PROF_RUNTIME_RESTART_V1",
                "project_id": "ai-prof-control-center",
                "probe_id": "runtime",
                "status": "verified",
                "response_class": "GREEN",
                "allowed_action": "restart_service",
                "target": "registered-service-only",
                "required_tests": ["health PASS"],
                "rollback": "restore previous service state",
                "fault_injection_evidence": ["FI-001 PASS"],
                "rollback_verified": True,
            }]}), encoding="utf-8"
        )
        (root / "orchestrator" / "repair_operation_bindings.json").write_text(
            json.dumps({"version": 1, "bindings": rows}), encoding="utf-8"
        )
        return root

    def _health_as_restart_binding(self) -> dict:
        return {
            "binding_id": "AI_PROF_RUNTIME_RESTART_BINDING_V1",
            "project_id": "ai-prof-control-center",
            "probe_id": "runtime",
            "suggested_action": "SERVICE_RESTART",
            "operation_profile": "ai-prof-control-center-health-check",
            "operation_kind": "service-restart",
            "required_runbook_id": "AI_PROF_RUNTIME_RESTART_V1",
            "task_scope": ["README.md"],
        }

    def test_empty_registry_grants_no_privileged_authority(self):
        root = self._root([])
        self.assertEqual(bindings.load_operation_bindings(root), {})
        self.assertIsNone(
            bindings.binding_for(
                root,
                "ai-prof-control-center",
                "runtime",
                "SERVICE_RESTART",
                ["AI_PROF_RUNTIME_RESTART_V1"],
            )
        )

    def test_health_check_profile_cannot_be_reused_as_service_restart(self):
        root = self._root([self._health_as_restart_binding()])
        with self.assertRaisesRegex(bindings.OperationBindingError, "not compatible"):
            bindings.load_operation_bindings(root)

    def test_unknown_operation_profile_is_rejected(self):
        item = self._health_as_restart_binding()
        item["operation_profile"] = "does-not-exist"
        root = self._root([item])
        with self.assertRaisesRegex(bindings.OperationBindingError, "unknown operation profile"):
            bindings.load_operation_bindings(root)

    def test_duplicate_privileged_route_is_rejected_before_ambiguity(self):
        first = self._health_as_restart_binding()
        second = dict(first)
        second["binding_id"] = "AI_PROF_RUNTIME_RESTART_BINDING_V2"
        root = self._root([first, second])
        with self.assertRaises(bindings.OperationBindingError):
            bindings.load_operation_bindings(root)

    def test_repository_ships_with_zero_privileged_repair_bindings(self):
        loaded = bindings.load_operation_bindings(ROOT)
        self.assertEqual(loaded, {})

    def test_binding_authority_files_are_not_self_maintenance_scope(self):
        payload = json.loads((ROOT / "orchestrator/projects.json").read_text(encoding="utf-8"))
        project = next(
            item for item in payload["projects"]
            if item["project_id"] == "ai-prof-control-center"
        )
        allowed = set(project["allowed_scope"])
        self.assertNotIn("orchestrator/repair_operation_bindings.json", allowed)
        self.assertNotIn("orchestrator/repair_operation_bindings.py", allowed)
        self.assertNotIn("orchestrator/repair_operations_bridge.py", allowed)


if __name__ == "__main__":
    unittest.main()
