#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import submit_task as legacy
import submit_task_v2 as v2
import telegram_bridge_v4 as bridge_v4


class SubmitTaskV2Tests(unittest.TestCase):
    def test_missing_allowlisted_parent_tail_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            self.assertEqual(
                v2.validate_scope_path_v2(
                    project,
                    "docs/AI_PROF_E2E_SMOKE_V3.md",
                    ["README.md", "docs/**"],
                ),
                "docs/AI_PROF_E2E_SMOKE_V3.md",
            )

    def test_nested_missing_tail_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            self.assertEqual(
                v2.validate_scope_path_v2(
                    project,
                    "docs/generated/smoke/result.md",
                    ["docs/**"],
                ),
                "docs/generated/smoke/result.md",
            )

    def test_unsafe_paths_still_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            for path in (
                "../escape.md",
                "/etc/passwd",
                r"docs\\escape.md",
                "outside/file.md",
            ):
                with self.subTest(path=path), self.assertRaises(legacy.IntakeError):
                    v2.validate_scope_path_v2(project, path, ["docs/**"])

    def test_existing_symlink_prefix_is_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            docs = project / "docs"
            docs.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (docs / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(legacy.IntakeError):
                v2.validate_scope_path_v2(
                    project,
                    "docs/link/new.md",
                    ["docs/**"],
                )

    def test_telegram_v4_routes_to_intake_v2(self) -> None:
        self.assertEqual(
            bridge_v4.SUBMIT_TASK_V2,
            ROOT / "orchestrator/submit_task_v2.py",
        )


if __name__ == "__main__":
    unittest.main()
