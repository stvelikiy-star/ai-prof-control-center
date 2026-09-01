from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_packet
import incident_diagnosis_runner as diagnosis_runner
import incident_engine
import monitoring_engine as monitor
import repair_operations_bridge as bridge


def task_values(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            result[key] = value
    return result


class RepairOperationsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "control"
        self.project = self.base / "project"
        self.state = self.base / "state"
        self.project.mkdir()
        (self.project / "README.md").write_text("demo\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.project, check=True)
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.project, check=True)
        (self.root / "orchestrator").mkdir(parents=True)
        (self.root / "agents" / "demo").mkdir(parents=True)
        self._write_registry()
        self._write_monitoring()
        self._write_policy("GREEN")
        self._write_runbook()
        self._write_empty_bindings()
        self.codex = self.base / "codex"
        self.codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.codex.chmod(self.codex.stat().st_mode | stat.S_IXUSR)

    def _write_registry(self):
        (self.root / "orchestrator" / "projects.json").write_text(
            json.dumps({"version": 1, "projects": [{
                "project_id": "demo",
                "path": str(self.project),
                "base_branch": "main",
                "allowed_base_branches": ["main"],
                "work_prefixes": ["feature/", "fix/"],
                "allowed_scope": ["README.md"],
                "agent_context": "agents/demo",
                "allow_commits": False,
                "allow_push": False,
                "allow_merge": False,
                "allow_deployment": False,
            }]}), encoding="utf-8"
        )

    def _write_monitoring(self):
        (self.root / "orchestrator" / "monitoring_profiles.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {
                "enabled": True,
                "probes": [{"id": "runtime", "kind": "path_exists", "path": str(self.project)}],
            }}}), encoding="utf-8"
        )

    def _write_policy(self, value: str):
        (self.root / "orchestrator" / "repair_policies.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {"runtime": value}}}),
            encoding="utf-8",
        )

    def _write_runbook(self):
        (self.root / "orchestrator" / "repair_runbooks.json").write_text(
            json.dumps({"version": 1, "runbooks": [{
                "runbook_id": "DEMO_RUNTIME_RESTART_V1",
                "project_id": "demo",
                "probe_id": "runtime",
                "status": "verified",
                "response_class": "GREEN",
                "allowed_action": "restart_service",
                "target": "demo.service",
                "required_tests": ["health PASS"],
                "rollback": "restore previous service state",
                "fault_injection_evidence": ["FI-DEMO-001 PASS"],
                "rollback_verified": True,
            }]}), encoding="utf-8"
        )

    def _write_empty_bindings(self):
        (self.root / "orchestrator" / "repair_operation_bindings.json").write_text(
            json.dumps({"version": 1, "bindings": []}), encoding="utf-8"
        )

    def _diagnosis_result(self, *, confidence: float = 0.96, action: str = "SERVICE_RESTART") -> Path:
        observation = monitor.Observation(
            project_id="demo",
            probe_id="runtime",
            kind="path_exists",
            severity="critical",
            ok=False,
            checked_at="2026-09-01T10:00:00+00:00",
            latency_ms=1,
            detail="service unhealthy",
            fingerprint="demo:runtime",
        )
        _, incident = incident_engine.apply_observation(self.state, observation)
        incident_engine.write_summary(self.state)
        packet = diagnosis_packet.generate_packets(self.root, self.state)[0]
        stdout = json.dumps({
            "version": 1,
            "incident_id": incident.incident_id,
            "root_cause": "registered service is stopped",
            "confidence": confidence,
            "repairable": True,
            "evidence": [{"source": "README.md", "finding": "service identity confirmed by fixture"}],
            "suggested_action": action,
            "residual_risks": ["health must pass after restart"],
        })

        def invoke(_codex, _project, _prompt):
            return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")

        result = diagnosis_runner.process_packet(
            self.root,
            self.state,
            packet,
            codex_cli=self.codex,
            invoke_fn=invoke,
        )
        self.assertEqual(result.status, "diagnosed")
        return Path(result.result_path)

    def _binding(self):
        return {
            "binding_id": "DEMO_RUNTIME_RESTART_BINDING_V1",
            "project_id": "demo",
            "probe_id": "runtime",
            "suggested_action": "SERVICE_RESTART",
            "operation_profile": "demo-service-restart",
            "operation_kind": "service-restart",
            "required_runbook_id": "DEMO_RUNTIME_RESTART_V1",
            "task_scope": ["README.md"],
        }

    def _profile(self, kind="service-restart"):
        return SimpleNamespace(
            key="demo-service-restart",
            kind=kind,
            repository=self.project,
        )

    def test_empty_binding_registry_blocks_privileged_task(self):
        result_path = self._diagnosis_result()
        result = bridge.bridge_result(self.root, self.state, result_path)
        self.assertEqual(result.status, "blocked")
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("no verified privileged operation binding", blocked["error"])
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])

    def test_yellow_policy_cannot_create_privileged_task(self):
        self._write_policy("YELLOW")
        result_path = self._diagnosis_result(confidence=0.96)
        result = bridge.bridge_result(
            self.root,
            self.state,
            result_path,
            binding_lookup=lambda *_args: self._binding(),
            profile_resolver=lambda _key: self._profile(),
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])

    def test_injected_verified_binding_builds_standard_operations_task(self):
        result_path = self._diagnosis_result()
        result = bridge.bridge_result(
            self.root,
            self.state,
            result_path,
            binding_lookup=lambda *_args: self._binding(),
            profile_resolver=lambda _key: self._profile(),
        )
        self.assertEqual(result.status, "created")
        values = task_values(Path(result.path).read_text(encoding="utf-8"))
        self.assertEqual(values["Execution-Mode"], "operations")
        self.assertEqual(values["Operation-Profile"], "demo-service-restart")
        self.assertEqual(values["Owner-Approval-Required"], "yes")
        self.assertEqual(values["Scope-Files"], "README.md")
        self.assertEqual(values["Repair-Response-Class"], "GREEN")
        self.assertEqual(values["Repair-Operation-Binding"], "DEMO_RUNTIME_RESTART_BINDING_V1")
        self.assertEqual(values["Repair-Runbook-IDs"], "DEMO_RUNTIME_RESTART_V1")

    def test_profile_kind_change_after_binding_validation_blocks(self):
        result_path = self._diagnosis_result()
        result = bridge.bridge_result(
            self.root,
            self.state,
            result_path,
            binding_lookup=lambda *_args: self._binding(),
            profile_resolver=lambda _key: self._profile(kind="control-center-health-check"),
        )
        self.assertEqual(result.status, "blocked")
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("kind changed", blocked["error"])

    def test_recovered_incident_cannot_create_operation(self):
        result_path = self._diagnosis_result()
        recovery = monitor.Observation(
            project_id="demo",
            probe_id="runtime",
            kind="path_exists",
            severity="critical",
            ok=True,
            checked_at="2026-09-01T10:01:00+00:00",
            latency_ms=1,
            detail="healthy",
            fingerprint="demo:runtime",
        )
        incident_engine.apply_observation(self.state, recovery)
        incident_engine.write_summary(self.state)
        result = bridge.bridge_result(
            self.root,
            self.state,
            result_path,
            binding_lookup=lambda *_args: self._binding(),
            profile_resolver=lambda _key: self._profile(),
        )
        self.assertEqual(result.status, "blocked")

    def test_exactly_once_for_same_bound_operation(self):
        result_path = self._diagnosis_result()
        kwargs = {
            "binding_lookup": lambda *_args: self._binding(),
            "profile_resolver": lambda _key: self._profile(),
        }
        first = bridge.bridge_result(self.root, self.state, result_path, **kwargs)
        second = bridge.bridge_result(self.root, self.state, result_path, **kwargs)
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(second.status, "already_pending")
        self.assertEqual(len(list((self.state / "queue" / "pending").glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
