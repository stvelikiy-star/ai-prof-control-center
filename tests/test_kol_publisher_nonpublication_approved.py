from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import approved_task_publisher_gate as gate


class KolOwnerReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.approved = Path(self.temp.name) / "approved"
        self.approved.mkdir()
        self.paths = SimpleNamespace(approved=self.approved)

    def add_task(self, name, suffix=""):
        p = self.approved / (name + ".md")
        p.write_text(
            "Task-ID: " + name + "\n"
            "Project-Path: " + str(gate.publisher.KOL_PROJECT) + "\n"
            "Owner-Approval-Required: yes\n" + suffix,
            encoding="utf-8",
        )
        return p

    def test_generic_approved_is_owner_review_not_failure(self):
        self.add_task("KOL_GENERIC")
        with patch.object(gate.publisher, "process_task") as send:
            self.assertEqual(gate._process_one_owner_aware(self.paths), 0)
            send.assert_not_called()

    def test_partial_v4_metadata_is_not_silently_ignored(self):
        task = self.add_task("KOL_PARTIAL", "Publication-Action: pull-request\n")
        with patch.object(gate.publisher, "process_task", side_effect=ValueError("incomplete metadata")) as send, \
             patch.object(gate.publisher, "write_log") as logs:
            self.assertEqual(gate._process_one_owner_aware(self.paths), 1)
            send.assert_called_once_with(self.paths, task)
            logs.assert_called_once()

    def test_v4_can_progress_while_generic_waits(self):
        self.add_task("AAA_GENERIC")
        task = self.add_task("ZZZ_V4", "Publication-Contract-Version: 4\n")
        with patch.object(gate.publisher, "process_task", return_value=0) as send:
            self.assertEqual(gate._process_one_owner_aware(self.paths), 0)
            send.assert_called_once_with(self.paths, task)

    def test_different_project_is_ignored(self):
        p = self.approved / "OTHER.md"
        p.write_text("Project-Path: /tmp/other\n")
        with patch.object(gate.publisher, "process_task") as send:
            self.assertEqual(gate._process_one_owner_aware(self.paths), 0)
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
