from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator import control_loop_service_night as night_service


class NightSingleFlightApprovedTests(unittest.TestCase):
    def test_approved_maintenance_code_task_blocks_new_stage01a(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            approved = runtime / "queue/approved"
            approved.mkdir(parents=True)
            (approved / "TASK.md").write_text(
                "Execution-Mode: code\n"
                f"Project-Path: {night_service.MAINTENANCE_PROJECT_PATH}\n",
                encoding="utf-8",
            )

            self.assertTrue(
                night_service._maintenance_code_task_in_flight(runtime)
            )

    def test_approved_other_project_does_not_block_maintenance_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            approved = runtime / "queue/approved"
            approved.mkdir(parents=True)
            (approved / "TASK.md").write_text(
                "Execution-Mode: code\n"
                "Project-Path: /home/agent/projects/other\n",
                encoding="utf-8",
            )

            self.assertFalse(
                night_service._maintenance_code_task_in_flight(runtime)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
