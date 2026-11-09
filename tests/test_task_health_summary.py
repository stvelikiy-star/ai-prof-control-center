from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import task_health_summary as health

TASK = "KOL_TRAVEL_PLATFORM_20261109T063719Z_7899A5"
OLD = "KOL_TRAVEL_PLATFORM_20261108T155123Z_E77D7E"

class TaskHealthSummaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.queue = self.root / "queue"
        for name in ("failed", "approved", "completed"):
            (self.queue / name).mkdir(parents=True)
        self.logs = self.root / "logs/orchestrator"
        self.logs.mkdir(parents=True)

    def task(self, kind, task_id):
        path = self.queue / kind / (task_id + ".md")
        path.write_text("Task-ID: " + task_id + "\n")
        return path

    def test_new_approved_with_verified_evidence_not_failed(self):
        fail = self.task("failed", OLD)
        good = self.task("approved", TASK)
        for phase, marker in health.PASS_MARKERS.items():
            (self.logs / (TASK + "-" + phase + "-x.log")).write_text(marker + "\n")
        result = health.summarize(self.root)
        self.assertEqual(result[0], "REVIEW_PASS|" + TASK)
        self.assertEqual(result[1], "HISTORIC_FAILURE|" + OLD)
        self.assertTrue(fail.exists() and good.exists())

    def test_approved_without_evidence_not_success(self):
        self.task("approved", TASK)
        self.assertEqual(health.summarize(self.root), ["REVIEW_PENDING|" + TASK])

    def test_failure_most_recent_not_hidden(self):
        self.task("failed", OLD)
        self.assertEqual(health.summarize(self.root), ["FAILED|" + OLD])

    def test_completed_success(self):
        self.task("completed", TASK)
        self.assertEqual(health.summarize(self.root), ["PASS|" + TASK])

    def test_empty_unknown(self):
        self.assertEqual(health.summarize(self.root), ["UNKNOWN|no-verified-tasks"])

    def test_symlink_not_followed(self):
        self.task("failed", OLD)
        (self.queue / "approved" / (TASK + ".md")).symlink_to(self.queue / "failed" / (OLD + ".md"))
        self.assertEqual(health.summarize(self.root), ["FAILED|" + OLD])

if __name__ == "__main__":
    unittest.main()
