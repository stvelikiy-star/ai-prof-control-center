from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_packet
import incident_diagnosis_runner as runner
import incident_engine
import monitoring_engine as monitor


class IncidentDiagnosisRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.root = self.base / "control"
        self.state = self.base / "state"
        self.project.mkdir()
        (self.root / "orchestrator").mkdir(parents=True)
        (self.root / "agents" / "demo").mkdir(parents=True)
        self._write_registry()
        self._write_monitoring()
        self._write_policy("YELLOW")
        self._write_runbooks([])
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
                "enabled": True,
                "agent_context": "agents/demo",
                "allow_commits": False,
                "allow_push": False,
                "allow_merge": False,
                "allow_deployment": False,
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

    def _green_runbook(self) -> dict:
        return {
            "runbook_id": "DEMO_RUNTIME_REPAIR_V1",
            "project_id": "demo",
            "probe_id": "runtime",
            "status": "verified",
            "response_class": "GREEN",
            "allowed_action": "code_patch",
            "target": "scoped project code",
            "required_tests": ["unit tests PASS", "health PASS"],
            "rollback": "restore previous commit",
            "fault_injection_evidence": ["FI-DEMO-001 PASS"],
            "rollback_verified": True,
        }

    def _open_incident_and_packet(self) -> Path:
        observation = monitor.Observation(
            project_id="demo",
            probe_id="runtime",
            kind="path_exists",
            severity="critical",
            ok=False,
            checked_at="2026-09-01T10:00:00+00:00",
            latency_ms=2,
            detail="runtime health failure",
            fingerprint="demo:runtime",
        )
        incident_engine.apply_observation(self.state, observation)
        incident_engine.write_summary(self.state)
        paths = diagnosis_packet.generate_packets(self.root, self.state)
        self.assertEqual(len(paths), 1)
        return paths[0]

    @staticmethod
    def _diagnosis_json(
        incident_id: str,
        *,
        confidence: float = 0.9,
        repairable: bool = True,
        action: str = "CODE_REPAIR",
        evidence: list[dict] | None = None,
        root_cause: str = "scoped regression",
    ) -> str:
        if evidence is None:
            evidence = [{"source": "src/app.py", "finding": "failing branch identified"}]
        return json.dumps({
            "version": 1,
            "incident_id": incident_id,
            "root_cause": root_cause,
            "confidence": confidence,
            "repairable": repairable,
            "evidence": evidence,
            "suggested_action": action,
            "residual_risks": ["requires post-repair tests"],
        })

    def _invoke_with(self, stdout: str, counter: list[int] | None = None):
        def invoke(_codex: Path, project: Path, prompt: str):
            self.assertEqual(project, self.project)
            self.assertIn("read-only incident diagnostician", prompt)
            self.assertIn("BEGIN UNTRUSTED INCIDENT EVIDENCE", prompt)
            if counter is not None:
                counter[0] += 1
            return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        return invoke

    def test_yellow_high_confidence_prepares_owner_review(self):
        packet = self._open_incident_and_packet()
        incident_id = json.loads(packet.read_text(encoding="utf-8"))["incident_id"]
        output = self._diagnosis_json(
            incident_id,
            root_cause="api_key=secret-value caused a scoped regression",
        )
        result = runner.process_packet(
            self.root, self.state, packet, codex_cli=self.codex, invoke_fn=self._invoke_with(output)
        )
        self.assertEqual(result.status, "diagnosed")
        record = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        self.assertEqual(record["effective_next_action"], "PREPARE_REPAIR_FOR_OWNER_REVIEW")
        self.assertIn("[REDACTED]", record["diagnosis"]["root_cause"])
        self.assertFalse(packet.exists())

    def test_red_is_diagnosed_but_never_prepared_for_repair(self):
        self._write_policy("RED")
        packet = self._open_incident_and_packet()
        payload = json.loads(packet.read_text(encoding="utf-8"))
        self.assertTrue(payload["diagnosis_required"])
        self.assertFalse(payload["repair_preparation_allowed"])
        output = self._diagnosis_json(payload["incident_id"], confidence=0.99)
        result = runner.process_packet(
            self.root, self.state, packet, codex_cli=self.codex, invoke_fn=self._invoke_with(output)
        )
        record = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        self.assertEqual(record["response_class"], "RED")
        self.assertEqual(record["effective_next_action"], "OWNER_ACTION_REQUIRED")

    def test_low_confidence_cannot_prepare_repair(self):
        packet = self._open_incident_and_packet()
        incident_id = json.loads(packet.read_text(encoding="utf-8"))["incident_id"]
        output = self._diagnosis_json(incident_id, confidence=0.50)
        result = runner.process_packet(
            self.root, self.state, packet, codex_cli=self.codex, invoke_fn=self._invoke_with(output)
        )
        record = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        self.assertEqual(record["effective_next_action"], "OWNER_ACTION_REQUIRED")

    def test_green_needs_high_confidence_and_verified_compatible_runbook(self):
        self._write_policy("GREEN")
        self._write_runbooks([self._green_runbook()])
        packet = self._open_incident_and_packet()
        incident_id = json.loads(packet.read_text(encoding="utf-8"))["incident_id"]
        output = self._diagnosis_json(incident_id, confidence=0.95, action="CODE_REPAIR")
        result = runner.process_packet(
            self.root, self.state, packet, codex_cli=self.codex, invoke_fn=self._invoke_with(output)
        )
        record = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        self.assertEqual(record["effective_next_action"], "GREEN_RUNBOOK_CANDIDATE")
        self.assertEqual(record["eligible_runbooks"], ["DEMO_RUNTIME_REPAIR_V1"])

    def test_green_below_green_threshold_falls_back_to_owner_review(self):
        self._write_policy("GREEN")
        self._write_runbooks([self._green_runbook()])
        packet = self._open_incident_and_packet()
        incident_id = json.loads(packet.read_text(encoding="utf-8"))["incident_id"]
        output = self._diagnosis_json(incident_id, confidence=0.80)
        result = runner.process_packet(
            self.root, self.state, packet, codex_cli=self.codex, invoke_fn=self._invoke_with(output)
        )
        record = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        self.assertEqual(record["effective_next_action"], "PREPARE_REPAIR_FOR_OWNER_REVIEW")

    def test_stale_or_tampered_policy_blocks_before_codex(self):
        packet = self._open_incident_and_packet()
        self._write_policy("RED")
        counter = [0]
        incident_id = json.loads(packet.read_text(encoding="utf-8"))["incident_id"]
        output = self._diagnosis_json(incident_id)
        result = runner.process_packet(
            self.root,
            self.state,
            packet,
            codex_cli=self.codex,
            invoke_fn=self._invoke_with(output, counter),
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(counter[0], 0)
        blocked = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        self.assertIn("stale or tampered", blocked["error"])

    def test_high_confidence_repair_without_evidence_is_blocked(self):
        packet = self._open_incident_and_packet()
        incident_id = json.loads(packet.read_text(encoding="utf-8"))["incident_id"]
        output = self._diagnosis_json(incident_id, confidence=0.95, evidence=[])
        result = runner.process_packet(
            self.root, self.state, packet, codex_cli=self.codex, invoke_fn=self._invoke_with(output)
        )
        self.assertEqual(result.status, "blocked")
        blocked = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        self.assertIn("requires evidence", blocked["error"])

    def test_malformed_packet_is_quarantined_once_without_codex(self):
        pending = self.state / "diagnosis" / "pending"
        pending.mkdir(parents=True)
        packet = pending / "INC-DEMO-AAAAAAAAAA.json"
        packet.write_text("{not-json", encoding="utf-8")
        counter = [0]
        result = runner.process_once(
            self.root,
            self.state,
            codex_cli=self.codex,
            invoke_fn=self._invoke_with("{}", counter),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(counter[0], 0)
        self.assertFalse(packet.exists())
        self.assertTrue((self.state / "diagnosis" / "quarantine" / packet.name).exists())
        self.assertIsNone(runner.process_once(self.root, self.state, codex_cli=self.codex))

    def test_invalid_model_protocol_is_blocked_and_archived(self):
        packet = self._open_incident_and_packet()
        result = runner.process_packet(
            self.root,
            self.state,
            packet,
            codex_cli=self.codex,
            invoke_fn=self._invoke_with("not-json"),
        )
        self.assertEqual(result.status, "blocked")
        self.assertFalse(packet.exists())
        self.assertTrue((self.state / "diagnosis" / "blocked_packets" / packet.name).exists())

    def test_existing_result_is_idempotent_and_does_not_reinvoke_codex(self):
        packet = self._open_incident_and_packet()
        payload = json.loads(packet.read_text(encoding="utf-8"))
        result_dir = self.state / "diagnosis" / "results"
        result_dir.mkdir(parents=True)
        existing = result_dir / f"{payload['incident_id']}.json"
        existing.write_text("{}", encoding="utf-8")
        counter = [0]
        result = runner.process_packet(
            self.root,
            self.state,
            packet,
            codex_cli=self.codex,
            invoke_fn=self._invoke_with("{}", counter),
        )
        self.assertEqual(result.status, "already_analyzed")
        self.assertEqual(counter[0], 0)


if __name__ == "__main__":
    unittest.main()
