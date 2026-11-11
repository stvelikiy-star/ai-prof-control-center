from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Canonical runtime executes Control Center entrypoints directly from
# orchestrator/, where sibling imports such as ``import control_loop`` resolve
# as top-level modules. Reproduce that exact import model in this regression
# test so direct-file CI execution matches the real Night Watch runtime.
ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import control_loop_service_night as night_service


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

    def test_other_project_pending_can_progress_while_maintenance_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            approved = runtime / "queue/approved"
            pending = runtime / "queue/pending"
            approved.mkdir(parents=True)
            pending.mkdir(parents=True)
            (approved / "MAINTENANCE.md").write_text(
                "Execution-Mode: code\n"
                f"Project-Path: {night_service.MAINTENANCE_PROJECT_PATH}\n",
                encoding="utf-8",
            )
            (pending / "KOL.md").write_text(
                "Execution-Mode: code\n"
                f"Project-Path: {night_service.KOL_PROJECT_PATH}\n",
                encoding="utf-8",
            )
            self.assertTrue(night_service._maintenance_code_task_in_flight(runtime))
            self.assertEqual(night_service._next_pending_code_project(runtime), night_service.KOL_PROJECT_PATH)
            stages = [name for name, _ in night_service._commands_with_night_safe_ai_prof_gate(
                Path(tmp) / "source", runtime
            )]
            self.assertIn("stage_01a", stages)
    def test_maintenance_pending_stays_blocked_when_maintenance_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            for queue in ("approved", "pending"):
                folder = runtime / "queue" / queue
                folder.mkdir(parents=True)
                (folder / (queue + ".md")).write_text(
                    "Execution-Mode: code\n"
                    f"Project-Path: {night_service.MAINTENANCE_PROJECT_PATH}\n",
                    encoding="utf-8",
                )
            stages = [name for name, _ in night_service._commands_with_night_safe_ai_prof_gate(
                Path(tmp) / "source", runtime
            )]
            self.assertNotIn("stage_01a", stages)
    def test_untrusted_pending_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            pending = runtime / "queue/pending"
            pending.mkdir(parents=True)
            (pending / "BAD.md").symlink_to(Path(tmp) / "outside")
            self.assertEqual(
                night_service._next_pending_code_project(runtime),
                night_service.MAINTENANCE_PROJECT_PATH,
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
