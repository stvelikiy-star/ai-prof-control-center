from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.state = Path(self.tmp.name) / "state"
        self.project = Path(self.tmp.name) / "project"
        (self.root / "orchestrator").mkdir(parents=True)
        (self.state / "diagnosis" / "results").mkdir(parents=True)
        (self.project / "src").mkdir(parents=True)
        (self.project / "tests").mkdir()
        (self.project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.project / "src" / "other.py").write_text("VALUE = 2\n", encoding="utf-8")
        (self.project / "tests" / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")
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
                "allowed_scope": ["src/**", "tests/**"],
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
            "Task-ID": "REPAIR_DEMO_ABCDEF1234",
            "Execution-Mode": "code",
            "Project-Path": str(self.project),
            "Work-Branch": "fix/repair-inc-demo-abcdef1234",
            "Required-Checks": ", ".join(contract["required_checks"]),
            "Scope-Files": "src/app.py",
            "Repair-Origin": "incident",
            "Incident-ID": "INC-DEMO-ABCDEF1234",
            "Diagnosis-SHA256": "a" * 64,
            "Repair-Response-Class": "YELLOW",
            "Repair-Runbook-IDs": "none",
            "Test-Contract-ID": contract["contract_id"],
            "Test-Contract-SHA256": contract["sha256"],
            "Test-Contract-Outcome": contract["required_outcome"],
        }

    def _validate(self, data: dict[str, str], *, diagnosis_source: str = "src/app.py") -> None:
        payload = {"project_id": "demo"}
        diagnosis = {
            "incident_id": data["Incident-ID"],
            "evidence": [{"source": diagnosis_source}],
        }
        with mock.patch(
            "repair_task_bridge._read_json",
            return_value=(payload, "a" * 64),
        ), mock.patch(
            "repair_task_bridge._validate_result",
            return_value=(diagnosis, "YELLOW", []),
        ):
            orch.validate_incident_repair_contract(self.root, self.state, data)

    def test_exact_current_contract_and_evidence_binding_pass(self):
        self._validate(self._incident_data())

    def test_contract_change_after_task_creation_blocks(self):
        data = self._incident_data()
        self._write_contract("DEMO_CODE_V2", ["python3 -m unittest"])
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_TEST_CONTRACT_DRIFT"):
            self._validate(data)

    def test_registry_check_change_after_task_creation_blocks(self):
        data = self._incident_data()
        self._write_registry(["python3 -m unittest", "python3 -m compileall -q src"])
        self._write_contract("DEMO_CODE_V2", ["python3 -m unittest", "python3 -m compileall -q src"])
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_TEST_CONTRACT_DRIFT"):
            self._validate(data)

    def test_task_required_checks_tamper_blocks(self):
        data = self._incident_data()
        data["Required-Checks"] = "python3 -m unittest, true"
        with self.assertRaisesRegex(RuntimeError, "required_checks"):
            self._validate(data)

    def test_scope_tamper_to_test_file_blocks(self):
        data = self._incident_data()
        data["Scope-Files"] = "tests/test_app.py"
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*scope"):
            self._validate(data)

    def test_scope_tamper_to_other_safe_file_blocks(self):
        data = self._incident_data()
        data["Scope-Files"] = "src/other.py"
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*scope"):
            self._validate(data)

    def test_scope_tamper_to_allowed_directory_blocks(self):
        data = self._incident_data()
        data["Scope-Files"] = "src"
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*scope"):
            self._validate(data)

    def test_scope_tamper_outside_current_allowlist_blocks(self):
        data = self._incident_data()
        data["Scope-Files"] = "private.txt"
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*scope"):
            self._validate(data)

    def test_diagnosis_sha_tamper_blocks(self):
        data = self._incident_data()
        data["Diagnosis-SHA256"] = "b" * 64
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*diagnosis_sha256"):
            self._validate(data)

    def test_task_identity_tamper_blocks(self):
        data = self._incident_data()
        data["Task-ID"] = "REPAIR_DEMO_TAMPERED"
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*task_id"):
            self._validate(data)

    def test_work_branch_tamper_blocks(self):
        data = self._incident_data()
        data["Work-Branch"] = "fix/repair-inc-demo-other"
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*work_branch"):
            self._validate(data)

    def test_diagnosis_evidence_change_after_task_creation_blocks(self):
        data = self._incident_data()
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_EVIDENCE_DRIFT.*scope"):
            self._validate(data, diagnosis_source="src/other.py")

    def test_missing_scope_metadata_blocks(self):
        data = self._incident_data()
        del data["Scope-Files"]
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_REPAIR_TEST_CONTRACT_DRIFT"):
            self._validate(data)

    def test_ordinary_task_is_unchanged(self):
        orch.validate_incident_repair_contract(
            self.root,
            self.state,
            {"Execution-Mode": "code", "Project-Path": str(self.project), "Required-Checks": "anything"},
        )

    def test_reserved_metadata_without_incident_origin_blocks(self):
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_RESERVED_REPAIR_METADATA"):
            orch.validate_incident_repair_contract(
                self.root,
                self.state,
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
                self.state,
                {
                    "Execution-Mode": "code",
                    "Project-Path": str(self.project),
                    "Required-Checks": "python3 -m unittest",
                    "Repair-Origin": "manual",
                },
            )

    def test_process_one_calls_contract_gate_before_stage01a_pass(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        gate = source.index("validate_incident_repair_contract(paths.root, state_root, data)")
        pass_marker = source.index('"STAGE_01A_VALIDATION_PASS"', gate)
        self.assertLess(gate, pass_marker)


if __name__ == "__main__":
    unittest.main()
