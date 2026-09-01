from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import runbook_registry


class RunbookRegistryTests(unittest.TestCase):
    def _root(self, runbook: dict, policy: str = "RED") -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "orchestrator").mkdir()
        (root / "orchestrator" / "projects.json").write_text(
            json.dumps({"version": 1, "projects": [{
                "project_id": "demo",
                "path": "/tmp/demo",
                "base_branch": "main",
                "allowed_base_branches": ["main"],
                "enabled": True,
            }]}), encoding="utf-8"
        )
        (root / "orchestrator" / "monitoring_profiles.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {
                "enabled": True,
                "probes": [{"id": "heartbeat", "kind": "path_exists", "path": "/tmp/demo"}],
            }}}), encoding="utf-8"
        )
        (root / "orchestrator" / "repair_policies.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {"heartbeat": policy}}}),
            encoding="utf-8",
        )
        (root / "orchestrator" / "repair_runbooks.json").write_text(
            json.dumps({"version": 1, "runbooks": [runbook]}), encoding="utf-8"
        )
        return root

    def _base(self) -> dict:
        return {
            "runbook_id": "DEMO_HEARTBEAT_V1",
            "project_id": "demo",
            "probe_id": "heartbeat",
            "status": "draft",
            "response_class": "YELLOW",
            "allowed_action": "restart_service",
            "target": "demo.service",
            "required_tests": ["heartbeat fresh"],
            "rollback": "restore previous service state",
            "fault_injection_evidence": [],
            "rollback_verified": False,
        }

    def test_draft_yellow_is_valid_but_not_green_eligible(self):
        root = self._root(self._base(), policy="YELLOW")
        loaded = runbook_registry.load_runbooks(root)
        self.assertIn("DEMO_HEARTBEAT_V1", loaded)
        self.assertEqual(runbook_registry.eligible_green_runbooks(root, "demo", "heartbeat"), [])

    def test_green_without_evidence_is_rejected(self):
        item = self._base()
        item.update({"status": "verified", "response_class": "GREEN", "rollback_verified": True})
        root = self._root(item, policy="GREEN")
        with self.assertRaises(runbook_registry.RunbookError):
            runbook_registry.load_runbooks(root)

    def test_green_without_verified_rollback_is_rejected(self):
        item = self._base()
        item.update({
            "status": "verified",
            "response_class": "GREEN",
            "fault_injection_evidence": ["FI-001 PASS"],
            "rollback_verified": False,
        })
        root = self._root(item, policy="GREEN")
        with self.assertRaises(runbook_registry.RunbookError):
            runbook_registry.load_runbooks(root)

    def test_verified_green_runbook_is_blocked_by_red_policy(self):
        item = self._base()
        item.update({
            "status": "verified",
            "response_class": "GREEN",
            "fault_injection_evidence": ["FI-001 PASS"],
            "rollback_verified": True,
        })
        root = self._root(item, policy="RED")
        self.assertEqual(runbook_registry.eligible_green_runbooks(root, "demo", "heartbeat"), [])

    def test_verified_green_with_green_policy_is_eligible(self):
        item = self._base()
        item.update({
            "status": "verified",
            "response_class": "GREEN",
            "fault_injection_evidence": ["FI-001 PASS"],
            "rollback_verified": True,
        })
        root = self._root(item, policy="GREEN")
        eligible = runbook_registry.eligible_green_runbooks(root, "demo", "heartbeat")
        self.assertEqual(len(eligible), 1)


if __name__ == "__main__":
    unittest.main()
