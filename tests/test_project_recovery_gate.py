from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import project_recovery_gate as recovery


class ProjectRecoveryGateTests(unittest.TestCase):
    def _root(self, contract: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "orchestrator").mkdir()
        (root / "orchestrator" / "projects.json").write_text(
            json.dumps({"version": 1, "projects": [{
                "project_id": "demo",
                "path": "/tmp/demo",
                "base_branch": "main",
                "allowed_base_branches": ["main"],
                "enabled": True,
                "code_required_checks": ["npm test"],
            }]}), encoding="utf-8"
        )
        (root / "proof.txt").write_text(
            "checkpoint-marker\nrollback-marker\nrestore-marker\nfault-marker\n",
            encoding="utf-8",
        )
        (root / "orchestrator" / "project_recovery_contracts.json").write_text(
            json.dumps({"version": 1, "projects": [contract]}), encoding="utf-8"
        )
        return root

    def _contract(self, **updates) -> dict:
        item = {
            "project_id": "demo",
            "recovery_mode": "unverified",
            "verification_level": "none",
            "checkpoint_evidence": [],
            "rollback_evidence": [],
            "restore_test_evidence": [],
            "fault_injection_evidence": [],
            "production_ready": False,
        }
        item.update(updates)
        return item

    def _complete_contract(self, **updates) -> dict:
        item = self._contract(
            recovery_mode="verified",
            verification_level="staging",
            checkpoint_evidence=["proof.txt:checkpoint-marker"],
            rollback_evidence=["proof.txt:rollback-marker"],
            restore_test_evidence=["proof.txt:restore-marker"],
            fault_injection_evidence=["proof.txt:fault-marker"],
            production_ready=True,
        )
        item.update(updates)
        return item

    def test_unverified_contract_fails_closed_with_specific_blockers(self):
        root = self._root(self._contract())
        ready, blockers = recovery.recovery_readiness(root, "demo")
        self.assertFalse(ready)
        self.assertIn("RECOVERY_MODE_NOT_VERIFIED", blockers)
        self.assertIn("RECOVERY_VERIFICATION_LEVEL_INSUFFICIENT", blockers)
        self.assertIn("CHECKPOINT_EVIDENCE_MISSING", blockers)
        self.assertIn("ROLLBACK_EVIDENCE_MISSING", blockers)
        self.assertIn("RESTORE_TEST_EVIDENCE_MISSING", blockers)
        self.assertIn("FAULT_INJECTION_EVIDENCE_MISSING", blockers)
        self.assertIn("PRODUCTION_RECOVERY_NOT_APPROVED", blockers)

    def test_production_ready_cannot_be_declared_without_all_evidence(self):
        root = self._root(self._contract(
            recovery_mode="verified", verification_level="staging", production_ready=True
        ))
        with self.assertRaisesRegex(recovery.RecoveryGateError, "lacks evidence"):
            recovery.load_recovery_contracts(root)

    def test_shadow_verification_cannot_authorize_production(self):
        root = self._root(self._complete_contract(verification_level="shadow"))
        with self.assertRaisesRegex(recovery.RecoveryGateError, "requires staging verification"):
            recovery.load_recovery_contracts(root)

    def test_verified_complete_staging_contract_is_ready(self):
        root = self._root(self._complete_contract())
        ready, blockers = recovery.recovery_readiness(root, "demo")
        self.assertTrue(ready)
        self.assertEqual(blockers, [])
        self.assertEqual(recovery.require_recovery_ready(root, "demo")["verification_level"], "staging")

    def test_missing_evidence_file_is_rejected(self):
        root = self._root(self._complete_contract(
            checkpoint_evidence=["missing.txt:checkpoint-marker"]
        ))
        with self.assertRaisesRegex(recovery.RecoveryGateError, "missing or escaped"):
            recovery.load_recovery_contracts(root)

    def test_stale_evidence_marker_is_rejected(self):
        root = self._root(self._complete_contract(
            rollback_evidence=["proof.txt:not-present-marker"]
        ))
        with self.assertRaisesRegex(recovery.RecoveryGateError, "stale rollback_evidence evidence marker"):
            recovery.load_recovery_contracts(root)

    def test_symlink_evidence_is_rejected(self):
        root = self._root(self._complete_contract(
            restore_test_evidence=["proof-link.txt:restore-marker"]
        ))
        os.symlink(root / "proof.txt", root / "proof-link.txt")
        with self.assertRaisesRegex(recovery.RecoveryGateError, "symlink restore_test_evidence evidence rejected"):
            recovery.load_recovery_contracts(root)

    def test_parent_escape_evidence_is_rejected(self):
        root = self._root(self._complete_contract(
            fault_injection_evidence=["../outside.txt:fault-marker"]
        ))
        with self.assertRaisesRegex(recovery.RecoveryGateError, "unsafe fault_injection_evidence evidence path"):
            recovery.load_recovery_contracts(root)

    def test_repository_records_all_current_projects_but_none_as_production_ready(self):
        loaded = recovery.load_recovery_contracts(ROOT)
        self.assertEqual(
            set(loaded),
            {"ai-prof-control-center", "ak-bermet", "kol-travel-platform", "resort-os"},
        )
        self.assertTrue(all(item["production_ready"] is False for item in loaded.values()))

    def test_control_center_has_unit_recovery_code_but_not_staging_verification(self):
        ready, blockers = recovery.recovery_readiness(ROOT, "ai-prof-control-center")
        self.assertFalse(ready)
        self.assertNotIn("CHECKPOINT_EVIDENCE_MISSING", blockers)
        self.assertNotIn("ROLLBACK_EVIDENCE_MISSING", blockers)
        self.assertNotIn("RESTORE_TEST_EVIDENCE_MISSING", blockers)
        self.assertIn("FAULT_INJECTION_EVIDENCE_MISSING", blockers)
        self.assertIn("RECOVERY_VERIFICATION_LEVEL_INSUFFICIENT", blockers)
        self.assertIn("PRODUCTION_RECOVERY_NOT_APPROVED", blockers)

    def test_ak_bermet_prepare_evidence_does_not_fake_rollback_readiness(self):
        ready, blockers = recovery.recovery_readiness(ROOT, "ak-bermet")
        self.assertFalse(ready)
        self.assertNotIn("CHECKPOINT_EVIDENCE_MISSING", blockers)
        self.assertNotIn("RESTORE_TEST_EVIDENCE_MISSING", blockers)
        self.assertIn("ROLLBACK_EVIDENCE_MISSING", blockers)
        self.assertIn("FAULT_INJECTION_EVIDENCE_MISSING", blockers)
        self.assertIn("RECOVERY_VERIFICATION_LEVEL_INSUFFICIENT", blockers)

    def test_kol_and_resort_os_remain_unverified_without_invented_evidence(self):
        for project_id in ("kol-travel-platform", "resort-os"):
            ready, blockers = recovery.recovery_readiness(ROOT, project_id)
            self.assertFalse(ready)
            self.assertIn("RECOVERY_VERIFICATION_LEVEL_INSUFFICIENT", blockers)
            self.assertIn("CHECKPOINT_EVIDENCE_MISSING", blockers)
            self.assertIn("ROLLBACK_EVIDENCE_MISSING", blockers)
            self.assertIn("RESTORE_TEST_EVIDENCE_MISSING", blockers)
            self.assertIn("FAULT_INJECTION_EVIDENCE_MISSING", blockers)

    def test_recovery_authority_files_are_not_self_maintenance_scope(self):
        payload = json.loads((ROOT / "orchestrator/projects.json").read_text(encoding="utf-8"))
        project = next(item for item in payload["projects"] if item["project_id"] == "ai-prof-control-center")
        allowed = set(project["allowed_scope"])
        self.assertNotIn("orchestrator/project_recovery_contracts.json", allowed)
        self.assertNotIn("orchestrator/project_recovery_gate.py", allowed)


if __name__ == "__main__":
    unittest.main()
