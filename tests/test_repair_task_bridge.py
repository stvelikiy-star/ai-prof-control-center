from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_packet
import incident_diagnosis_runner as diagnosis_runner
import incident_engine
import monitoring_engine as monitor
import repair_task_bridge as bridge


def parse_task_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        values[key] = value
    return values


class RepairTaskBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.root = self.base / "control"
        self.state = self.base / "state"
        self.project.mkdir()
        (self.project / "src").mkdir()
        (self.project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.project, check=True)
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.project, check=True)

        (self.root / "orchestrator").mkdir(parents=True)
        context = self.root / "agents" / "demo"
        context.mkdir(parents=True)
        for name in (
            "SYSTEM_INSTRUCTIONS.md",
            "SOURCE_POLICY.md",
            "STATE.md",
            "APPROVAL_MATRIX.md",
            "DECISIONS.md",
        ):
            (context / name).write_text("test\n", encoding="utf-8")
        self._write_registry()
        self._write_monitoring()
        self._write_policy("YELLOW")
        self._write_runbooks([])
        self._write_test_contract()

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
                "allowed_scope": ["src/**"],
                "forbidden_scope": [".git/**", ".env", "secrets"],
                "agent_context": "agents/demo",
                "allow_commits": False,
                "allow_push": False,
                "allow_merge": False,
                "allow_deployment": False,
                "require_clean_repository": True,
                "max_scope_files": 20,
                "code_required_commands": ["git", "python3"],
                "code_required_checks": ["python3 -m unittest"],
            }]}),
            encoding="utf-8",
        )

    def _write_monitoring(self):
        (self.root / "orchestrator" / "monitoring_profiles.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {
                "enabled": True,
                "probes": [{
                    "id": "runtime",
                    "kind": "path_exists",
                    "path": str(self.project),
                    "severity": "critical",
                }],
            }}}),
            encoding="utf-8",
        )

    def _write_policy(self, response_class: str):
        (self.root / "orchestrator" / "repair_policies.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {"runtime": response_class}}}),
            encoding="utf-8",
        )

    def _write_runbooks(self, runbooks: list[dict]):
        (self.root / "orchestrator" / "repair_runbooks.json").write_text(
            json.dumps({"version": 1, "runbooks": runbooks}), encoding="utf-8"
        )

    def _write_test_contract(self):
        (self.root / "orchestrator" / "project_test_contracts.json").write_text(
            json.dumps({"version": 1, "contracts": [{
                "contract_id": "DEMO_CODE_V1",
                "project_id": "demo",
                "kind": "code_repair",
                "required_checks": ["python3 -m unittest"],
                "required_outcome": "STAGE_01C_AUDIT_PASS",
            }]}),
            encoding="utf-8",
        )

    def _green_runbook(self):
        return {
            "runbook_id": "DEMO_RUNTIME_REPAIR_V1",
            "project_id": "demo",
            "probe_id": "runtime",
            "status": "verified",
            "response_class": "GREEN",
            "allowed_action": "code_patch",
            "target": "src/app.py",
            "required_tests": ["unit PASS", "health PASS"],
            "rollback": "restore previous commit",
            "fault_injection_evidence": ["FI-DEMO-001 PASS"],
            "rollback_verified": True,
        }

    def _open_incident(self):
        observation = monitor.Observation(
            project_id="demo",
            probe_id="runtime",
            kind="path_exists",
            severity="critical",
            ok=False,
            checked_at="2026-09-01T10:00:00+00:00",
            latency_ms=2,
            detail="runtime failure",
            fingerprint="demo:runtime",
        )
        _, incident = incident_engine.apply_observation(self.state, observation)
        incident_engine.write_summary(self.state)
        return incident

    def _diagnose(
        self,
        *,
        confidence: float = 0.90,
        action: str = "CODE_REPAIR",
        source: str = "src/app.py",
        root_cause: str = "scoped regression",
    ) -> Path:
        incident = self._open_incident()
        packet = diagnosis_packet.generate_packets(self.root, self.state)[0]
        stdout = json.dumps({
            "version": 1,
            "incident_id": incident.incident_id,
            "root_cause": root_cause,
            "confidence": confidence,
            "repairable": True,
            "evidence": [{"source": source, "finding": "defect located"}],
            "suggested_action": action,
            "residual_risks": ["run tests"],
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

    def test_yellow_code_diagnosis_creates_existing_task_schema(self):
        diagnosis_result = self._diagnose(confidence=0.90)
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "created")
        task = Path(result.path)
        self.assertTrue(task.is_file())
        text = task.read_text(encoding="utf-8")
        values = parse_task_values(text)
        self.assertEqual(values["Execution-Mode"], "code")
        self.assertEqual(values["Project-Path"], str(self.project))
        self.assertTrue(values["Work-Branch"].startswith("fix/repair-inc-demo-"))
        self.assertEqual(values["Scope-Files"], "src/app.py")
        self.assertEqual(values["Owner-Approval-Required"], "yes")
        self.assertTrue(values["Incident-ID"].startswith("INC-DEMO-"))
        self.assertRegex(values["Diagnosis-SHA256"], r"^[0-9a-f]{64}$")
        self.assertEqual(values["Repair-Response-Class"], "YELLOW")
        self.assertEqual(values["Test-Contract-ID"], "DEMO_CODE_V1")
        self.assertRegex(values["Test-Contract-SHA256"], r"^[0-9a-f]{64}$")
        self.assertEqual(values["Test-Contract-Outcome"], "STAGE_01C_AUDIT_PASS")
        self.assertEqual(values["Required-Checks"], "python3 -m unittest")
        self.assertIn("deployment", values["Out-of-Scope"])

    def test_bridge_is_exactly_once_across_existing_queue_task(self):
        diagnosis_result = self._diagnose()
        first = bridge.bridge_result(self.root, self.state, diagnosis_result)
        second = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(second.status, "already_pending")
        pending = list((self.state / "queue" / "pending").glob("*.md"))
        self.assertEqual(len(pending), 1)

    def test_test_contract_drift_blocks_task_creation(self):
        diagnosis_result = self._diagnose()
        contract_path = self.root / "orchestrator" / "project_test_contracts.json"
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
        payload["contracts"][0]["required_checks"] = ["python3 -m unittest", "bash -c unsafe"]
        contract_path.write_text(json.dumps(payload), encoding="utf-8")
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("test contract drift", blocked["error"])

    def test_evidence_outside_allowlist_cannot_become_scope(self):
        other = self.project / "private.txt"
        other.write_text("secret\n", encoding="utf-8")
        diagnosis_result = self._diagnose(source="private.txt")
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("no existing allowlisted code evidence path", blocked["error"])

    def test_nonexistent_model_source_cannot_authorize_new_file(self):
        diagnosis_result = self._diagnose(source="src/invented.py")
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])

    def test_policy_change_to_red_blocks_previously_diagnosed_repair(self):
        diagnosis_result = self._diagnose()
        self._write_policy("RED")
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "blocked")
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("stale or tampered", blocked["error"])

    def test_tampered_effective_action_is_rejected(self):
        diagnosis_result = self._diagnose()
        payload = json.loads(diagnosis_result.read_text(encoding="utf-8"))
        payload["effective_next_action"] = "GREEN_RUNBOOK_CANDIDATE"
        diagnosis_result.write_text(json.dumps(payload), encoding="utf-8")
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "blocked")
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("effective action is stale or tampered", blocked["error"])

    def test_green_code_repair_revalidates_verified_runbook(self):
        self._write_policy("GREEN")
        self._write_runbooks([self._green_runbook()])
        diagnosis_result = self._diagnose(confidence=0.96)
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "created")
        text = Path(result.path).read_text(encoding="utf-8")
        self.assertIn("Repair-Response-Class: GREEN", text)
        self.assertIn("Repair-Runbook-IDs: DEMO_RUNTIME_REPAIR_V1", text)
        self.assertIn("Test-Contract-ID: DEMO_CODE_V1", text)

    def test_service_restart_diagnosis_is_not_converted_to_code_task(self):
        diagnosis_result = self._diagnose(action="SERVICE_RESTART")
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "blocked")
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("non-code diagnosis", blocked["error"])
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])

    def test_recovered_incident_cannot_spawn_repair_task(self):
        diagnosis_result = self._diagnose()
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
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "blocked")
        blocked = json.loads(Path(result.path).read_text(encoding="utf-8"))
        self.assertIn("incident is no longer open", blocked["error"])

    def test_root_cause_is_normalized_to_single_task_line(self):
        diagnosis_result = self._diagnose(root_cause="first line\nsecond line")
        result = bridge.bridge_result(self.root, self.state, diagnosis_result)
        self.assertEqual(result.status, "created")
        text = Path(result.path).read_text(encoding="utf-8")
        instruction_line = next(line for line in text.splitlines() if line.startswith("Instructions:"))
        self.assertIn("first line second line", instruction_line)


if __name__ == "__main__":
    unittest.main()
