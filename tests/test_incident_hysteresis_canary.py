from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import incident_engine as base
import incident_engine_canary as canary
import incident_hysteresis as hysteresis
import monitoring_engine as monitor


class IncidentHysteresisCanaryTests(unittest.TestCase):
    def observation(self, ok: bool, checked_at: str, detail: str = "detail"):
        return monitor.Observation(
            project_id="demo",
            probe_id="heartbeat",
            kind="heartbeat_json",
            severity="critical",
            ok=ok,
            checked_at=checked_at,
            latency_ms=10,
            detail=detail,
            fingerprint="demo:heartbeat",
        )

    def test_single_failure_is_pending_and_does_not_open_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = canary.reconcile(
                root,
                [self.observation(False, "2026-09-01T00:00:00+00:00")],
            )
            self.assertEqual(result, [("pending_failure", None)])
            self.assertEqual(base.summary(root)["open_count"], 0)
            state = hysteresis.load(root, "demo:heartbeat")
            self.assertEqual(state.consecutive_failures, 1)

    def test_replayed_or_out_of_order_observation_cannot_advance_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canary.reconcile(
                root,
                [self.observation(False, "2026-09-01T00:01:00+00:00")],
            )
            for checked_at in (
                "2026-09-01T00:01:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ):
                with self.subTest(checked_at=checked_at):
                    with self.assertRaisesRegex(
                        hysteresis.HysteresisStateError,
                        "replayed or out-of-order",
                    ):
                        canary.reconcile(
                            root,
                            [self.observation(False, checked_at)],
                        )
            state = hysteresis.load(root, "demo:heartbeat")
            self.assertEqual(state.consecutive_failures, 1)
            self.assertEqual(base.summary(root)["open_count"], 0)

    def test_two_consecutive_failures_open_one_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canary.reconcile(
                root,
                [self.observation(False, "2026-09-01T00:00:00+00:00")],
            )
            transition, incident = canary.reconcile(
                root,
                [self.observation(False, "2026-09-01T00:01:00+00:00")],
            )[0]
            self.assertEqual(transition, "opened")
            self.assertIsNotNone(incident)
            self.assertEqual(base.summary(root)["open_count"], 1)

            transition2, incident2 = canary.reconcile(
                root,
                [self.observation(False, "2026-09-01T00:02:00+00:00")],
            )[0]
            self.assertEqual(transition2, "updated")
            self.assertEqual(incident2.incident_id, incident.incident_id)
            self.assertEqual(base.summary(root)["open_count"], 1)

    def test_two_successes_are_required_to_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canary.reconcile(root, [self.observation(False, "2026-09-01T00:00:00+00:00")])
            _, opened = canary.reconcile(
                root,
                [self.observation(False, "2026-09-01T00:01:00+00:00")],
            )[0]

            transition, pending = canary.reconcile(
                root,
                [self.observation(True, "2026-09-01T00:02:00+00:00", "first recovery")],
            )[0]
            self.assertEqual(transition, "pending_recovery")
            self.assertEqual(pending.incident_id, opened.incident_id)
            self.assertEqual(base.summary(root)["open_count"], 1)

            transition2, resolved = canary.reconcile(
                root,
                [self.observation(True, "2026-09-01T00:03:00+00:00", "confirmed")],
            )[0]
            self.assertEqual(transition2, "resolved")
            self.assertEqual(resolved.incident_id, opened.incident_id)
            summary = base.summary(root)
            self.assertEqual(summary["open_count"], 0)
            self.assertEqual(summary["resolved_count"], 1)

    def test_fail_pass_fail_does_not_create_episode_churn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = canary.reconcile(
                root, [self.observation(False, "2026-09-01T00:00:00+00:00")]
            )[0]
            healthy = canary.reconcile(
                root, [self.observation(True, "2026-09-01T00:01:00+00:00")]
            )[0]
            second = canary.reconcile(
                root, [self.observation(False, "2026-09-01T00:02:00+00:00")]
            )[0]
            self.assertEqual(first[0], "pending_failure")
            self.assertEqual(healthy, ("healthy", None))
            self.assertEqual(second[0], "pending_failure")
            summary = base.summary(root)
            self.assertEqual(summary["open_count"], 0)
            self.assertEqual(summary["resolved_count"], 0)

    def test_corrupt_hysteresis_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = hysteresis.state_path(root, "demo:heartbeat")
            path.parent.mkdir(parents=True)
            path.write_text("{bad json", encoding="utf-8")
            with self.assertRaises(hysteresis.HysteresisStateError):
                canary.reconcile(
                    root, [self.observation(False, "2026-09-01T00:00:00+00:00")]
                )

    def test_symlink_hysteresis_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            path = hysteresis.state_path(root, "demo:heartbeat")
            path.parent.mkdir(parents=True)
            path.symlink_to(outside)
            with self.assertRaises(hysteresis.HysteresisStateError):
                canary.reconcile(
                    root, [self.observation(False, "2026-09-01T00:00:00+00:00")]
                )

    def test_main_temporarily_installs_canary_reconcile_and_restores_baseline(self):
        original = base.reconcile

        def fake_main():
            self.assertIs(base.reconcile, canary.reconcile)
            return 17

        with mock.patch.object(base, "main", side_effect=fake_main):
            self.assertEqual(canary.main(), 17)
        self.assertIs(base.reconcile, original)


if __name__ == "__main__":
    unittest.main()
