from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import control_loop_service_night as night


V4_TASK = "\n".join(
    [
        "Task-ID: KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938",
        "Project-Path: /home/agent/Загрузки/kol-travel-platform",
        "Publication-Contract-Version: 4",
        "Work-Branch: feature/chatgpt-issue-172",
    ]
)


class KolV4ProjectIsolationTests(unittest.TestCase):
    def _runtime_with_v4(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        runtime = Path(tmp.name)
        pending = runtime / "queue/pending"
        pending.mkdir(parents=True)
        (pending / "task.md").write_text(V4_TASK + "\n", encoding="utf-8")
        return tmp, runtime

    def test_v4_task_is_detected_from_pending(self):
        tmp, runtime = self._runtime_with_v4()
        self.addCleanup(tmp.cleanup)
        self.assertTrue(night._kol_v4_task_in_flight(runtime))

    def test_ak_bermet_publishers_are_removed_during_kol_v4(self):
        tmp, runtime = self._runtime_with_v4()
        self.addCleanup(tmp.cleanup)
        established = [
            ("kol_approved_publisher_pre", ["kol-pre"]),
            ("ak_bermet_approved_publisher_pre", ["ak-pre"]),
            ("operations", ["python", "/repo/orchestrator/operations_runner.py"]),
            ("stage_01a", ["stage-01a"]),
            ("kol_approved_publisher_post", ["kol-post"]),
            ("ak_bermet_approved_publisher_post", ["ak-post"]),
        ]
        with mock.patch.object(
            night.base,
            "_commands_with_publishers",
            return_value=established,
        ), mock.patch.object(
            night.base,
            "_publisher_argv",
            return_value=["python", "ai-prof-publisher"],
        ):
            commands = night._commands_with_night_safe_ai_prof_gate(
                Path("/repo"), runtime
            )
        stages = [stage for stage, _argv in commands]
        self.assertNotIn("ak_bermet_approved_publisher_pre", stages)
        self.assertNotIn("ak_bermet_approved_publisher_post", stages)
        self.assertIn("kol_approved_publisher_pre", stages)
        self.assertIn("kol_approved_publisher_post", stages)
        self.assertIn("stage_01a", stages)

    def test_campaign_tick_is_suppressed_during_kol_v4(self):
        tmp, runtime = self._runtime_with_v4()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(
            night,
            "_ORIGINAL_CAMPAIGN_TICK_ALL",
            return_value=17,
        ) as delegated:
            self.assertEqual(night._night_campaign_tick_all(Path("/repo"), runtime), 0)
        delegated.assert_not_called()

    def test_campaign_tick_delegates_without_kol_v4(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            night,
            "_ORIGINAL_CAMPAIGN_TICK_ALL",
            return_value=17,
        ) as delegated:
            runtime = Path(tmp)
            self.assertEqual(night._night_campaign_tick_all(Path("/repo"), runtime), 17)
        delegated.assert_called_once_with(Path("/repo"), runtime)


if __name__ == "__main__":
    unittest.main(verbosity=2)
