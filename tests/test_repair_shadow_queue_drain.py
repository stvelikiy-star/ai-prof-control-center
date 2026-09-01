from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_queue_drain as diagnosis_drain
import repair_task_bridge_drain as bridge_drain

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "systemd" / "ai-prof-repair-diagnosis.service"


class RepairShadowQueueDrainTests(unittest.TestCase):
    @staticmethod
    def diagnosis_result(index: int, status: str = "diagnosed"):
        return diagnosis_drain.base.ProcessResult(
            f"INC-DEMO-{index:010X}", status, f"/state/result-{index}.json"
        )

    @staticmethod
    def bridge_result(index: int, status: str = "created"):
        return bridge_drain.base.BridgeResult(
            f"INC-DEMO-{index:010X}", status, f"TASK_{index}", f"/state/task-{index}.md"
        )

    def test_diagnosis_drain_processes_multiple_fast_packets_sequentially(self):
        sequence = [self.diagnosis_result(1), self.diagnosis_result(2), None]
        clock = iter([0.0, 10.0, 20.0])
        with mock.patch.object(diagnosis_drain.base, "process_once", side_effect=sequence) as process:
            results = diagnosis_drain.drain(
                Path("/control"),
                Path("/state"),
                monotonic_fn=lambda: next(clock),
            )
        self.assertEqual([item.incident_id for item in results], [
            "INC-DEMO-0000000001",
            "INC-DEMO-0000000002",
        ])
        self.assertEqual(process.call_count, 3)

    def test_diagnosis_drain_does_not_start_second_codex_without_full_timeout_margin(self):
        clock = iter([0.0, 200.0])
        with mock.patch.object(
            diagnosis_drain.base,
            "process_once",
            return_value=self.diagnosis_result(1),
        ) as process:
            results = diagnosis_drain.drain(
                Path("/control"),
                Path("/state"),
                monotonic_fn=lambda: next(clock),
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(diagnosis_drain.MIN_REMAINING_FOR_NEW_CODEX, 960)

    def test_diagnosis_drain_stops_on_blocked_to_avoid_cascade(self):
        with mock.patch.object(
            diagnosis_drain.base,
            "process_once",
            side_effect=[self.diagnosis_result(1, "blocked"), self.diagnosis_result(2)],
        ) as process:
            results = diagnosis_drain.drain(
                Path("/control"),
                Path("/state"),
                monotonic_fn=lambda: 0.0,
            )
        self.assertEqual([item.status for item in results], ["blocked"])
        self.assertEqual(process.call_count, 1)

    def test_diagnosis_drain_has_hard_packet_cap(self):
        with mock.patch.object(
            diagnosis_drain.base,
            "process_once",
            side_effect=[self.diagnosis_result(index) for index in range(1, 6)],
        ) as process:
            results = diagnosis_drain.drain(
                Path("/control"),
                Path("/state"),
                monotonic_fn=lambda: 0.0,
            )
        self.assertEqual(len(results), diagnosis_drain.MAX_PACKETS_PER_RUN)
        self.assertEqual(process.call_count, diagnosis_drain.MAX_PACKETS_PER_RUN)

    def test_diagnosis_limits_fail_closed(self):
        with self.assertRaises(diagnosis_drain.DiagnosisDrainError):
            diagnosis_drain.drain(Path("/control"), Path("/state"), max_packets=5)
        with self.assertRaises(diagnosis_drain.DiagnosisDrainError):
            diagnosis_drain.drain(
                Path("/control"),
                Path("/state"),
                budget_seconds=diagnosis_drain.MIN_REMAINING_FOR_NEW_CODEX - 1,
            )

    def test_bridge_drain_advances_multiple_results_but_stops_on_blocked(self):
        with mock.patch.object(
            bridge_drain.base,
            "process_once",
            side_effect=[
                self.bridge_result(1),
                self.bridge_result(2),
                self.bridge_result(3, "blocked"),
                self.bridge_result(4),
            ],
        ) as process:
            results = bridge_drain.drain(Path("/control"), Path("/state"))
        self.assertEqual([item.status for item in results], ["created", "created", "blocked"])
        self.assertEqual(process.call_count, 3)

    def test_bridge_drain_has_hard_result_cap(self):
        with mock.patch.object(
            bridge_drain.base,
            "process_once",
            side_effect=[self.bridge_result(index) for index in range(1, 18)],
        ) as process:
            results = bridge_drain.drain(Path("/control"), Path("/state"))
        self.assertEqual(len(results), bridge_drain.MAX_RESULTS_PER_RUN)
        self.assertEqual(process.call_count, bridge_drain.MAX_RESULTS_PER_RUN)

    def test_service_budget_leaves_systemd_kill_margin_and_uses_only_sequential_wrappers(self):
        text = SERVICE.read_text(encoding="utf-8")
        self.assertIn("diagnosis_queue_drain.py", text)
        self.assertIn("repair_task_bridge_drain.py", text)
        self.assertIn("TimeoutStartSec=20min", text)
        self.assertLessEqual(diagnosis_drain.SERVICE_BUDGET_SECONDS + 100, 20 * 60)
        self.assertEqual(
            diagnosis_drain.MIN_REMAINING_FOR_NEW_CODEX,
            diagnosis_drain.base.cr.CODEX_TIMEOUT_SECONDS + diagnosis_drain.SAFETY_MARGIN_SECONDS,
        )
        source = (ORCHESTRATOR / "diagnosis_queue_drain.py").read_text(encoding="utf-8")
        self.assertNotIn("threading", source)
        self.assertNotIn("concurrent.futures", source)
        self.assertNotIn("multiprocessing", source)


if __name__ == "__main__":
    unittest.main()
