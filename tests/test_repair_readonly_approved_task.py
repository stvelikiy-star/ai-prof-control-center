from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"orchestrator"))
import repair_readonly_approved_task as repair

class RepairReadOnlyApprovedTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.state = Path(self.t.name)
        self.path = self.state/"queue/approved"/(repair.TASK_ID+".md")
        self.path.parent.mkdir(parents=True)
        (self.state/"queue/blocked").mkdir(parents=True)
        self.original = "\n".join([
            "Task-ID: "+repair.TASK_ID,
            "Execution-Mode: code",
            "Operation-Profile: none",
            "Project-Path: "+repair.PROJECT_PATH,
            "Work-Branch: "+repair.WORK_BRANCH,
            "Owner-Approval-Required: yes",
            "Scope-Files: README.md",
            "Instructions: Проверь. Ничего не удаляй и не изменяй.",
            "",
        ])
        self.path.write_text(self.original)
    def test_dry_run_keeps_source(self):
        self.assertIn("DRY_RUN_PASS",repair.quarantine(self.state))
        self.assertEqual(self.path.read_text(),self.original)
    def test_exact_identity_moves_to_blocked_with_reason(self):
        self.assertIn("APPLIED",repair.quarantine(self.state,apply=True))
        dst=self.state/"queue/blocked"/self.path.name
        self.assertFalse(self.path.exists())
        self.assertTrue(dst.read_text().startswith(self.original))
        self.assertIn(repair.REASON,dst.read_text())
    def test_no_clobber(self):
        (self.state/"queue/blocked"/self.path.name).write_text("valuable")
        with self.assertRaises(repair.QuarantineRefused):
            repair.quarantine(self.state,apply=True)
        self.assertTrue(self.path.exists())
    def test_mismatched_task_refused(self):
        self.path.write_text(self.original.replace(repair.WORK_BRANCH,"feature/other"))
        with self.assertRaises(repair.QuarantineRefused):
            repair.quarantine(self.state,apply=True)
        self.assertTrue(self.path.exists())

if __name__=="__main__": unittest.main()
