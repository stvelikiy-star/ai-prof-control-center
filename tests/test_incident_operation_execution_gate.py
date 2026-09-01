from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import guarded_operations_process as guarded
import incident_operation_execution_gate as gate
import operations_runner as operations
import operations_runner_night as night
import repair_operations_bridge as bridge


class IncidentOperationExecutionGateTests(unittest.TestCase):
    def setUp(self):
        self.project_path = "/srv/demo"
        self.incident_id = "INC-DEMO-ABCDEF1234"
        self.payload = {
            "project_id": "demo",
            "probe_id": "runtime-checkout",
            "response_class": "GREEN",
        }
        self.diagnosis = {
            "incident_id": self.incident_id,
            "suggested_action": "SERVICE_RESTART",
        }
        self.binding = {
            "binding_id": "DEMO_RESTART_V1",
            "project_id": "demo",
            "probe_id": "runtime-checkout",
            "suggested_action": "SERVICE_RESTART",
            "operation_profile": "demo-restart",
            "operation_kind": "service-restart",
            "required_runbook_id": "DEMO_RUNBOOK_V1",
            "task_scope": ["src/app.py"],
        }
        self.project = {
            "project_id": "demo",
            "path": self.project_path,
            "base_branch": "main",
            "agent_context": "agents/demo",
            "allowed_scope": ["src/**"],
        }
        self.profile = SimpleNamespace(
            key="demo-restart",
            kind="service-restart",
            repository=Path(self.project_path),
        )
        self.data = {
            "Task-ID": bridge._task_id("demo", self.incident_id),
            "Execution-Mode": "operations",
            "Operation-Profile": "demo-restart",
            "Project-Path": self.project_path,
            "Base-Branch": "main",
            "Work-Branch": bridge._work_branch(self.incident_id),
            "Agent-Context": "agents/demo",
            "Scope-Files": "src/app.py",
            "Repair-Origin": "incident-operation",
            "Incident-ID": self.incident_id,
            "Diagnosis-SHA256": "a" * 64,
            "Repair-Response-Class": "GREEN",
            "Repair-Operation-Binding": "DEMO_RESTART_V1",
            "Repair-Runbook-IDs": "DEMO_RUNBOOK_V1",
        }

    def _validate(self, data=None, **overrides):
        kwargs = {
            "result_reader": lambda _path: (self.payload, "a" * 64),
            "diagnosis_validator": lambda _root, _state, _payload: (
                self.diagnosis,
                ["DEMO_RUNBOOK_V1"],
            ),
            "binding_loader": lambda _root: {"DEMO_RESTART_V1": self.binding},
            "profile_resolver": lambda _key: self.profile,
            "registry_reader": lambda _root: {"demo": self.project},
            "fixed_scope_resolver": lambda _project, scope: list(scope),
        }
        kwargs.update(overrides)
        gate.validate_incident_operation_authority(
            Path("/control"), Path("/state"), data or dict(self.data), **kwargs
        )

    def test_exact_current_authority_passes(self):
        self._validate()

    def test_ordinary_operation_without_reserved_repair_metadata_is_unchanged(self):
        gate.validate_incident_operation_authority(
            Path("/control"),
            Path("/state"),
            {"Execution-Mode": "operations", "Operation-Profile": "manual-profile"},
        )

    def test_reserved_metadata_without_origin_blocks(self):
        data = {"Execution-Mode": "operations", "Incident-ID": self.incident_id}
        with self.assertRaisesRegex(gate.IncidentOperationGateError, "reserved metadata"):
            gate.validate_incident_operation_authority(Path("/control"), Path("/state"), data)

    def test_authority_drift_blocks(self):
        cases = {
            "diagnosis_sha": ("Diagnosis-SHA256", "b" * 64),
            "response_class": ("Repair-Response-Class", "YELLOW"),
            "binding": ("Repair-Operation-Binding", "OTHER_BINDING"),
            "runbook": ("Repair-Runbook-IDs", "OTHER_RUNBOOK"),
            "profile": ("Operation-Profile", "other-profile"),
            "project": ("Project-Path", "/srv/other"),
            "base": ("Base-Branch", "develop"),
            "context": ("Agent-Context", "agents/other"),
            "task_id": ("Task-ID", "REPAIR_OP_DEMO_OTHER"),
            "work_branch": ("Work-Branch", "fix/other"),
            "scope": ("Scope-Files", "src/other.py"),
        }
        for name, (field, value) in cases.items():
            with self.subTest(name=name):
                data = dict(self.data)
                data[field] = value
                with self.assertRaises(gate.IncidentOperationGateError):
                    self._validate(data)

    def test_current_diagnosis_or_binding_disappearance_blocks(self):
        with self.assertRaisesRegex(gate.IncidentOperationGateError, "diagnosis"):
            self._validate(result_reader=lambda _path: (_ for _ in ()).throw(ValueError("gone")))
        with self.assertRaisesRegex(gate.IncidentOperationGateError, "binding"):
            self._validate(binding_loader=lambda _root: {})

    def test_guarded_processor_blocks_before_profile_resolution_or_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "control"
            state = Path(tmp) / "state"
            root.mkdir()
            paths = operations.orch.build_paths(root, state)
            task = paths.pending / "REPAIR_OP_DEMO_TEST.md"
            task.write_text(
                "\n".join(
                    [
                        "Task-ID: REPAIR_OP_DEMO_TEST",
                        "Execution-Mode: operations",
                        "Operation-Profile: demo-restart",
                        "Project-Path: /srv/demo",
                        "Base-Branch: main",
                        "Work-Branch: fix/repair-op-test",
                        "Agent-Context: agents/demo",
                        "Goal: demo",
                        "Scope: demo",
                        "Out-of-Scope: none",
                        "Pass-Criteria: pass",
                        "Required-Checks: none",
                        "Required-Commands: git, python3",
                        "Required-Environment: none",
                        "Owner-Approval-Required: yes",
                        "Scope-Files: src/app.py",
                        "Repair-Origin: incident-operation",
                        "Incident-ID: INC-DEMO-ABCDEF1234",
                        "Diagnosis-SHA256: " + "a" * 64,
                        "Repair-Response-Class: GREEN",
                        "Repair-Operation-Binding: DEMO_RESTART_V1",
                        "Repair-Runbook-IDs: DEMO_RUNBOOK_V1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    guarded,
                    "validate_incident_operation_authority",
                    side_effect=gate.IncidentOperationGateError("BLOCKED_TEST"),
                ),
                mock.patch.object(operations, "resolve_profile") as resolve,
                mock.patch.object(operations, "execute_profile") as execute,
            ):
                result = guarded.process_one(operations, paths)
            self.assertEqual(result, 1)
            resolve.assert_not_called()
            execute.assert_not_called()
            self.assertTrue((paths.blocked / task.name).is_file())

    def test_canonical_night_entrypoint_installs_guarded_processor(self):
        original_process = night.base.process_one
        original_run = night.base.run_argv
        try:
            with mock.patch.object(night.base, "main", return_value=17):
                self.assertEqual(night.main(), 17)
            self.assertIs(night.base.process_one, night._guarded_process_one)
            self.assertIs(night.base.run_argv, night._night_run_argv)
        finally:
            night.base.process_one = original_process
            night.base.run_argv = original_run


if __name__ == "__main__":
    unittest.main()
