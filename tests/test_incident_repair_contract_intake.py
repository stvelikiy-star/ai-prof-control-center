from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR_DIR))
MODULE_PATH = ORCHESTRATOR_DIR / "orchestrator.py"
SPEC = importlib.util.spec_from_file_location("incident_contract_orchestrator", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load orchestrator")
orch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = orch
SPEC.loader.exec_module(orch)

from project_test_contracts import contract_for_project


class IncidentRepairContractIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "control"
        self.project = Path(self.tmp.name) / "project"
        (self.root / "orchestrator").mkdir(parents=True)
        self.project.mkdir()
        self._write_registry(["python3 -m unittest"])
        self._write_contract("DEMO_CODE_V1", ["python3 -m unittest"])

    def _write_registry(self, checks: list[str]) -> None:
        payload = {
            "version": 1,
            "projects": [{
                "project_id": "demo",
                "path": str(self.project),
                "enabled": True,
                "base_branch": "main",
                "allowed_base_branches": ["main"],
                "local_integration_branches": [],
                "work_prefixes": ["feature/", "fix/"],
                "allowed_scope": ["src/**"],
                "forbidden_scope": [".git/**"],
                "agent_context": "agents/demo",
                "allow_commits": False,
                "allow_push": False,
                "allow_merge": False,
                "allow_deployment": False,
                "require_clean_repository": True,
                "max_scope_files": 20,
                "code_required_commands": ["git", "python3"],
                "code_required_checks": checks,
            }],
        }
        (self.root / "orchestrator" / "projects.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _write_contract(self, contract_id: str, checks: list[str]) -> None:
        payload = {
            "version": 1,
            "contracts": [{
                "contract_id": contract_id,
                "project_id": "demo",
                "kind": "code_repair",
                "required_checks": checks,
                "required_outcome": "STAGE_01C_AUDIT_PASS",
            }],
        }
        (self.root / "orchestrator" / "project_test_contracts.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _incident_data(self) -> dict[str, str]:
        contract = contract_for_project(self.root, "demo")
        return {
            "Execution-Mode": "code",
            "Project-Path": str(self.project),
            "Required-Checks": ", ".join(contract["required_checks"]),
            "Repair-Origin": "incident",
            "Incident-ID": "INC-DEMO-ABCDEF1234",
            "Diagnosis-SHA256": "a" * 64,
            "Repair-Response-Class": "YELLOW",
            "Repair-Runbook-IDs": "none",
            "Test-Contract-ID": contract["contract_id"],
            "Test-Contract-SHA256": contract["sha256"],
            "Test-Contract-Outcome": contract["required_outcome"],
        }

    def test_exact_current_contract_passes(self):
        orch.validate_incident_repair_contract(self.root, self._incident_data())

    def test_contract_change_after_task_creation_blocks(self):
        data = self._incident_data()
        self._write_contract("DEMO_CODE_V2", ["python3 -m unittest"])
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_TEST_CONTRACT_DRIFT"):
            orch.validate_incident_repair_contract(self.root, data)

    def test_registry_check_change_after_task_creation_blocks(self):
        data = self._incident_data()
        self._write_registry(["python3 -m unittest", "python3 -m compileall -q src"])
        self._write_contract(
            "DEMO_CODE_V2",
            ["python3 -m unittest", "python3 -m compileall -q src"],
        )
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_TEST_CONTRACT_DRIFT"):
            orch.validate_incident_repair_contract(self.root, data)

    def test_task_required_checks_tamper_blocks(self):
        data = self._incident_data()
        data["Required-Checks"] = "python3 -m unittest, true"
        with self.assertRaisesRegex(RuntimeError, "required_checks"):
            orch.validate_incident_repair_contract(self.root, data)

    def test_ordinary_task_is_unchanged(self):
        orch.validate_incident_repair_contract(
            self.root,
            {
                "Execution-Mode": "code",
                "Project-Path": str(self.project),
                "Required-Checks": "anything",
            },
        )

    def test_reserved_metadata_without_incident_origin_blocks(self):
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_RESERVED_REPAIR_METADATA"):
            orch.validate_incident_repair_contract(
                self.root,
                {
                    "Execution-Mode": "code",
                    "Project-Path": str(self.project),
                    "Required-Checks": "python3 -m unittest",
                    "Test-Contract-ID": "DEMO_CODE_V1",
                },
            )

    def test_nonincident_origin_cannot_use_reserved_contract_metadata(self):
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_RESERVED_REPAIR_METADATA"):
            orch.validate_incident_repair_contract(
                self.root,
                {
                    "Execution-Mode": "code",
                    "Project-Path": str(self.project),
                    "Required-Checks": "python3 -m unittest",
                    "Repair-Origin": "manual",
                },
            )

    def test_process_one_calls_contract_gate_before_stage01a_pass(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        gate = source.index("validate_incident_repair_contract(paths.root, data)")
        pass_marker = source.index('"STAGE_01A_VALIDATION_PASS"', gate)
        self.assertLess(gate, pass_marker)


if __name__ == "__main__":
    unittest.main()
