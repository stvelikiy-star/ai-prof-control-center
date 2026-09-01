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
import repair_task_bridge_drain as bridge_drain
import shadow_queue_health as health


class RepairOwnerActionTerminalTests(unittest.TestCase):
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

        (self.root / "orchestrator" / "projects.json").write_text(
            json.dumps({
                "version": 1,
                "projects": [{
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
                }],
            }),
            encoding="utf-8",
        )
        (self.root / "orchestrator" / "monitoring_profiles.json").write_text(
            json.dumps({
                "version": 1,
                "projects": {
                    "demo": {
                        "enabled": True,
                        "probes": [{
                            "id": "runtime",
                            "kind": "path_exists",
                            "path": str(self.project),
                            "severity": "critical",
                        }],
                    }
                },
            }),
            encoding="utf-8",
        )
        (self.root / "orchestrator" / "repair_runbooks.json").write_text(
            json.dumps({"version": 1, "runbooks": []}),
            encoding="utf-8",
        )
        (self.root / "orchestrator" / "project_test_contracts.json").write_text(
            json.dumps({
                "version": 1,
                "contracts": [{
                    "contract_id": "DEMO_CODE_V1",
                    "project_id": "demo",
                    "kind": "code_repair",
                    "required_checks": ["python3 -m unittest"],
                    "required_outcome": "STAGE_01C_AUDIT_PASS",
                }],
            }),
            encoding="utf-8",
        )

        self.codex = self.base / "codex"
        self.codex.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.codex.chmod(self.codex.stat().st_mode | stat.S_IXUSR)

    def _write_policy(self, response_class: str) -> None:
        (self.root / "orchestrator" / "repair_policies.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {"runtime": response_class}}}),
            encoding="utf-8",
        )

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
        transition, incident = incident_engine.apply_observation(self.state, observation)
        self.assertEqual(transition, "opened")
        self.assertIsNotNone(incident)
        incident_engine.write_summary(self.state)
        return incident

    def _diagnose(self, *, response_class: str, confidence: float) -> tuple[Path, str]:
        self._write_policy(response_class)
        incident = self._open_incident()
        packet = diagnosis_packet.generate_packets(self.root, self.state)[0]
        stdout = json.dumps({
            "version": 1,
            "incident_id": incident.incident_id,
            "root_cause": "scoped synthetic regression",
            "confidence": confidence,
            "repairable": True,
            "evidence": [{"source": "src/app.py", "finding": "defect located"}],
            "suggested_action": "CODE_REPAIR",
            "residual_risks": ["owner gate remains required"],
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
        result_path = Path(result.result_path)
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        return result_path, payload["effective_next_action"]

    def _assert_owner_terminal(self) -> None:
        results = bridge_drain.drain(self.root, self.state)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.status, "owner_action_required")
        self.assertEqual(result.task_id, "")
        terminal = Path(result.path)
        self.assertTrue(terminal.is_file())
        payload = json.loads(terminal.read_text(encoding="utf-8"))
        self.assertEqual(payload["effective_next_action"], "OWNER_ACTION_REQUIRED")
        self.assertTrue(payload["owner_action_required"])
        self.assertFalse(payload["task_created"])
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])
        self.assertEqual(list((self.state / "repair_bridge" / "blocked").glob("*.json")), [])
        self.assertIsNone(bridge.process_once(self.root, self.state))

        snapshot = health.build_snapshot(self.state)
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["state"], "healthy")
        self.assertEqual(snapshot["bridge"]["unprocessed_count"], 0)
        self.assertEqual(snapshot["bridge"]["blocked_open_count"], 0)
        self.assertEqual(snapshot["reasons"], [])

    def test_red_owner_action_is_terminal_not_blocked_or_tasked(self):
        _, action = self._diagnose(response_class="RED", confidence=0.99)
        self.assertEqual(action, "OWNER_ACTION_REQUIRED")
        self._assert_owner_terminal()

    def test_low_confidence_yellow_owner_action_is_terminal_not_backlog(self):
        _, action = self._diagnose(response_class="YELLOW", confidence=0.50)
        self.assertEqual(action, "OWNER_ACTION_REQUIRED")
        self._assert_owner_terminal()

    def test_policy_drift_still_fails_closed_instead_of_terminalizing_stale_result(self):
        result_path, action = self._diagnose(response_class="YELLOW", confidence=0.90)
        self.assertEqual(action, "PREPARE_REPAIR_FOR_OWNER_REVIEW")
        self._write_policy("RED")
        result = bridge.bridge_result(self.root, self.state, result_path)
        self.assertEqual(result.status, "blocked")
        self.assertFalse((self.state / "repair_bridge" / "terminal" / f"{result.incident_id}.json").exists())
        self.assertEqual(list((self.state / "queue" / "pending").glob("*.md")), [])

    def test_terminal_action_allowlist_is_exactly_owner_action_required(self):
        self.assertEqual(bridge.TERMINAL_ACTIONS, {"OWNER_ACTION_REQUIRED"})
        self.assertEqual(
            bridge.REPAIR_ACTIONS,
            {"PREPARE_REPAIR_FOR_OWNER_REVIEW", "GREEN_RUNBOOK_CANDIDATE"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
