from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import incident_engine as incidents
import monitoring_engine as monitor


class IncidentEngineTests(unittest.TestCase):
    def _observation(
        self,
        ok: bool,
        detail: str = "detail",
        severity: str = "warning",
        checked_at: str | None = None,
    ):
        return monitor.Observation(
            project_id="paladin",
            probe_id="n8n-executions",
            kind="path_exists",
            severity=severity,
            ok=ok,
            checked_at=checked_at or monitor.utc_now(),
            latency_ms=12,
            detail=detail,
            fingerprint="paladin:n8n-executions",
        )

    def test_first_failure_opens_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            transition, incident = incidents.apply_observation(Path(tmp), self._observation(False))
            self.assertEqual(transition, "opened")
            self.assertIsNotNone(incident)
            self.assertEqual(incident.status, "open")
            self.assertEqual(incident.failure_count, 1)

    def test_repeated_failure_updates_same_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, first = incidents.apply_observation(root, self._observation(False, "one", checked_at="2026-09-01T00:00:00+00:00"))
            transition, second = incidents.apply_observation(root, self._observation(False, "two", "critical", checked_at="2026-09-01T00:01:00+00:00"))
            self.assertEqual(transition, "updated")
            self.assertEqual(first.incident_id, second.incident_id)
            self.assertEqual(second.failure_count, 2)
            self.assertEqual(second.last_detail, "two")
            self.assertEqual(second.severity, "critical")
            self.assertEqual(incidents.summary(root)["open_count"], 1)

    def test_recovery_resolves_open_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, opened = incidents.apply_observation(root, self._observation(False, checked_at="2026-09-01T00:00:00+00:00"))
            transition, resolved = incidents.apply_observation(root, self._observation(True, "recovered", checked_at="2026-09-01T00:01:00+00:00"))
            self.assertEqual(transition, "resolved")
            self.assertEqual(opened.incident_id, resolved.incident_id)
            self.assertEqual(resolved.status, "resolved")
            summary = incidents.summary(root)
            self.assertEqual(summary["open_count"], 0)
            self.assertEqual(summary["resolved_count"], 1)

    def test_reopened_failure_gets_new_id_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, first = incidents.apply_observation(root, self._observation(False, checked_at="2026-09-01T00:00:00+00:00"))
            incidents.apply_observation(root, self._observation(True, checked_at="2026-09-01T00:01:00+00:00"))
            transition, second = incidents.apply_observation(root, self._observation(False, checked_at="2026-09-01T00:02:00+00:00"))
            self.assertEqual(transition, "opened")
            self.assertNotEqual(first.incident_id, second.incident_id)
            summary = incidents.summary(root)
            self.assertEqual(summary["resolved_count"], 1)
            self.assertEqual(summary["open_count"], 1)

    def test_healthy_without_incident_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            transition, incident = incidents.apply_observation(Path(tmp), self._observation(True))
            self.assertEqual(transition, "healthy")
            self.assertIsNone(incident)

    def test_different_probe_fingerprint_creates_different_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._observation(False)
            second = monitor.Observation(
                project_id="paladin",
                probe_id="website",
                kind="http_get",
                severity="critical",
                ok=False,
                checked_at=monitor.utc_now(),
                latency_ms=20,
                detail="500",
                fingerprint="paladin:website",
            )
            incidents.apply_observation(root, first)
            incidents.apply_observation(root, second)
            self.assertEqual(incidents.summary(root)["open_count"], 2)

    def test_summary_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incidents.apply_observation(root, self._observation(False))
            path = incidents.write_summary(root)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["open_count"], 1)
            self.assertEqual(len(payload["open_incidents"]), 1)


if __name__ == "__main__":
    unittest.main()
