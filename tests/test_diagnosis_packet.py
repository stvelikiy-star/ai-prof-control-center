from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_packet
import incident_engine
import monitoring_engine as monitor


class DiagnosisPacketTests(unittest.TestCase):
    def _root(self, project_path: Path) -> Path:
        root = project_path / "control"
        (root / "orchestrator").mkdir(parents=True)
        (root / "orchestrator" / "projects.json").write_text(
            json.dumps({"version": 1, "projects": [{
                "project_id": "demo",
                "path": str(project_path),
                "base_branch": "main",
                "allowed_base_branches": ["main"],
                "enabled": True,
                "agent_context": "agents/demo",
                "allow_commits": False,
                "allow_push": False,
                "allow_merge": False,
                "allow_deployment": False,
            }]}), encoding="utf-8"
        )
        (root / "orchestrator" / "monitoring_profiles.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {
                "enabled": True,
                "probes": [{"id": "runtime", "kind": "path_exists", "path": str(project_path)}],
            }}}), encoding="utf-8"
        )
        (root / "orchestrator" / "repair_policies.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {"runtime": "YELLOW"}}}),
            encoding="utf-8",
        )
        return root

    def test_packet_contains_only_safe_project_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            root = self._root(project)
            state = project / "state"
            observation = monitor.Observation(
                project_id="demo", probe_id="runtime", kind="path_exists",
                severity="warning", ok=False, checked_at=monitor.utc_now(),
                latency_ms=1, detail="missing", fingerprint="demo:runtime",
            )
            _, incident = incident_engine.apply_observation(state, observation)
            incident_engine.write_summary(state)
            paths = diagnosis_packet.generate_packets(root, state)
            self.assertEqual(len(paths), 1)
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["response_class"], "YELLOW")
            self.assertTrue(payload["diagnosis_required"])
            self.assertFalse(payload["owner_action_required"])
            self.assertNotIn("secret_file", payload["project"])
            self.assertIn("NO_PRODUCTION_MUTATION", payload["constraints"])

    def test_red_policy_requires_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            root = self._root(project)
            (root / "orchestrator" / "repair_policies.json").write_text(
                json.dumps({"version": 1, "projects": {"demo": {"runtime": "RED"}}}),
                encoding="utf-8",
            )
            state = project / "state"
            observation = monitor.Observation(
                project_id="demo", probe_id="runtime", kind="path_exists",
                severity="critical", ok=False, checked_at=monitor.utc_now(),
                latency_ms=1, detail="missing", fingerprint="demo:runtime",
            )
            incident_engine.apply_observation(state, observation)
            incident_engine.write_summary(state)
            path = diagnosis_packet.generate_packets(root, state)[0]
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["response_class"], "RED")
            self.assertTrue(payload["owner_action_required"])
            self.assertFalse(payload["diagnosis_required"])


if __name__ == "__main__":
    unittest.main()
