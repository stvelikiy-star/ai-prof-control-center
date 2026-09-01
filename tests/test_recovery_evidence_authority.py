from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import project_recovery_gate as recovery


class RecoveryEvidenceAuthorityTests(unittest.TestCase):
    def _root(self, allowed_scope: list[str], evidence_source: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "orchestrator").mkdir(parents=True)
        evidence_path = root / evidence_source
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            "checkpoint-marker\nrollback-marker\nrestore-marker\nfault-marker\n",
            encoding="utf-8",
        )
        projects = {
            "version": 1,
            "projects": [
                {
                    "project_id": "demo",
                    "path": "/tmp/demo",
                    "base_branch": "main",
                    "allowed_base_branches": ["main"],
                    "enabled": True,
                    "code_required_checks": ["npm test"],
                },
                {
                    "project_id": "ai-prof-control-center",
                    "path": "/tmp/control",
                    "base_branch": "maintenance/base",
                    "allowed_base_branches": ["maintenance/base"],
                    "enabled": True,
                    "allowed_scope": allowed_scope,
                    "code_required_checks": [],
                },
            ],
        }
        (root / "orchestrator" / "projects.json").write_text(
            json.dumps(projects), encoding="utf-8"
        )
        marker = lambda value: f"{evidence_source}:{value}"
        contract = {
            "version": 1,
            "projects": [{
                "project_id": "demo",
                "recovery_mode": "verified",
                "verification_level": "staging",
                "checkpoint_evidence": [marker("checkpoint-marker")],
                "rollback_evidence": [marker("rollback-marker")],
                "restore_test_evidence": [marker("restore-marker")],
                "fault_injection_evidence": [marker("fault-marker")],
                "production_ready": True,
            }],
        }
        (root / "orchestrator" / "project_recovery_contracts.json").write_text(
            json.dumps(contract), encoding="utf-8"
        )
        return root

    def test_exact_self_maintenance_scope_cannot_be_recovery_authority(self):
        root = self._root(["proof.txt"], "proof.txt")
        with self.assertRaisesRegex(recovery.RecoveryGateError, "self-maintenance-writable"):
            recovery.load_recovery_contracts(root)

    def test_glob_self_maintenance_scope_cannot_be_recovery_authority(self):
        root = self._root(["reports/**"], "reports/proof.txt")
        with self.assertRaisesRegex(recovery.RecoveryGateError, "self-maintenance-writable"):
            recovery.load_recovery_contracts(root)


if __name__ == "__main__":
    unittest.main()
