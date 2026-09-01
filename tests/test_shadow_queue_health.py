from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import incident_engine_shadow_health_canary as canary
import repair_policy
import shadow_queue_health as health

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


class ShadowQueueHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    @staticmethod
    def summary_with(*incident_ids: str) -> dict:
        return {
            "version": 1,
            "open_incidents": [{"incident_id": value} for value in incident_ids],
        }

    def _touch_json(self, relative: str, *, age_seconds: float = 0.0) -> Path:
        path = self.state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        stamp = NOW.timestamp() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_empty_shadow_queues_are_healthy(self):
        with mock.patch.object(health, "incident_summary", return_value=self.summary_with()):
            snapshot = health.build_snapshot(self.state, now=NOW)
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["state"], "healthy")
        self.assertEqual(snapshot["diagnosis"]["pending_count"], 0)
        self.assertEqual(snapshot["bridge"]["unprocessed_count"], 0)
        self.assertEqual(snapshot["reasons"], [])

    def test_open_diagnosis_blocked_is_immediately_degraded(self):
        incident_id = "INC-DEMO-AAAAAAAAAA"
        self._touch_json(f"diagnosis/blocked/{incident_id}.json")
        with mock.patch.object(health, "incident_summary", return_value=self.summary_with(incident_id)):
            snapshot = health.build_snapshot(self.state, now=NOW)
        self.assertFalse(snapshot["ok"])
        self.assertEqual(snapshot["diagnosis"]["blocked_open_count"], 1)
        self.assertIn("open_diagnosis_blocked", snapshot["reasons"])

    def test_historical_blocked_record_does_not_permanently_degrade_health(self):
        self._touch_json("diagnosis/blocked/INC-DEMO-AAAAAAAAAA.json")
        self._touch_json("repair_bridge/blocked/INC-DEMO-BBBBBBBBBB.json")
        with mock.patch.object(health, "incident_summary", return_value=self.summary_with()):
            snapshot = health.build_snapshot(self.state, now=NOW)
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["diagnosis"]["blocked_open_count"], 0)
        self.assertEqual(snapshot["bridge"]["blocked_open_count"], 0)

    def test_old_diagnosis_pending_is_degraded(self):
        self._touch_json(
            "diagnosis/pending/INC-DEMO-AAAAAAAAAA.json",
            age_seconds=health.MAX_PENDING_AGE_SECONDS + 1,
        )
        with mock.patch.object(health, "incident_summary", return_value=self.summary_with()):
            snapshot = health.build_snapshot(self.state, now=NOW)
        self.assertFalse(snapshot["ok"])
        self.assertIn("diagnosis_pending_too_old", snapshot["reasons"])

    def test_old_unprocessed_bridge_result_is_degraded(self):
        self._touch_json(
            "diagnosis/results/INC-DEMO-AAAAAAAAAA.json",
            age_seconds=health.MAX_PENDING_AGE_SECONDS + 1,
        )
        with mock.patch.object(health, "incident_summary", return_value=self.summary_with()):
            snapshot = health.build_snapshot(self.state, now=NOW)
        self.assertFalse(snapshot["ok"])
        self.assertEqual(snapshot["bridge"]["unprocessed_count"], 1)
        self.assertIn("repair_bridge_pending_too_old", snapshot["reasons"])

    def test_bridged_result_is_not_counted_as_unprocessed(self):
        incident_id = "INC-DEMO-AAAAAAAAAA"
        self._touch_json(f"diagnosis/results/{incident_id}.json", age_seconds=99999)
        self._touch_json(f"repair_bridge/tasks/{incident_id}.json")
        with mock.patch.object(health, "incident_summary", return_value=self.summary_with()):
            snapshot = health.build_snapshot(self.state, now=NOW)
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["bridge"]["unprocessed_count"], 0)

    def test_collection_error_writes_explicit_degraded_snapshot(self):
        with mock.patch.object(health, "_open_incident_ids", side_effect=health.ShadowQueueHealthError("bad")):
            path = health.write_current_health(self.state)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "degraded")
        self.assertEqual(payload["reasons"], ["health_collection_error:ShadowQueueHealthError"])

    def test_read_health_accepts_fresh_healthy_and_rejects_reported_degraded(self):
        healthy = {
            "version": 1,
            "timestamp": NOW.isoformat(),
            "ok": True,
            "state": "healthy",
            "thresholds": {},
            "diagnosis": {},
            "bridge": {},
            "reasons": [],
        }
        path = health.write_snapshot(self.state, healthy)
        ok, detail = health.read_health(self.state, now=NOW)
        self.assertTrue(ok)
        self.assertIn("reported_ok=True", detail)

        healthy["ok"] = False
        healthy["state"] = "degraded"
        health.write_snapshot(self.state, healthy)
        ok2, detail2 = health.read_health(self.state, now=NOW)
        self.assertFalse(ok2)
        self.assertIn("reported_ok=False", detail2)

    def test_read_health_rejects_stale_malformed_and_symlink(self):
        stale = {
            "version": 1,
            "timestamp": (NOW - timedelta(seconds=health.MAX_HEALTH_AGE_SECONDS + 1)).isoformat(),
            "ok": True,
            "state": "healthy",
            "thresholds": {},
            "diagnosis": {},
            "bridge": {},
            "reasons": [],
        }
        path = health.write_snapshot(self.state, stale)
        self.assertFalse(health.read_health(self.state, now=NOW)[0])
        path.write_text("{bad json", encoding="utf-8")
        self.assertFalse(health.read_health(self.state, now=NOW)[0])
        path.unlink()
        outside = self.state / "outside.json"
        outside.write_text(json.dumps(stale), encoding="utf-8")
        path.symlink_to(outside)
        self.assertFalse(health.read_health(self.state, now=NOW)[0])

    def test_canary_observation_has_fixed_identity_and_no_new_repair_authority(self):
        with mock.patch.object(health, "read_health", return_value=(False, "degraded")):
            observation = canary.shadow_health_observation(self.state)
        self.assertEqual(observation.project_id, "ai-prof-control-center")
        self.assertEqual(observation.probe_id, "shadow-queue-health")
        self.assertEqual(observation.fingerprint, "ai-prof-control-center:shadow-queue-health")
        self.assertEqual(observation.severity, "warning")
        self.assertFalse(observation.ok)
        self.assertEqual(
            repair_policy.classify(ROOT, "ai-prof-control-center", "shadow-queue-health"),
            "RED",
        )


if __name__ == "__main__":
    unittest.main()
