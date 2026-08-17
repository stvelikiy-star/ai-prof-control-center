#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import codex_stage01b_runner_v2 as v2
import control_loop_service


class CodexStage01BV2Tests(unittest.TestCase):
    def test_prompt_allows_new_file_inside_scoped_directory(self) -> None:
        prompt = v2.build_codex_implementation_input_v2(
            "Scope-Files: docs\nInstructions: create docs/AI_PROF_E2E_SMOKE.md"
        )
        self.assertIn("A Scope-Files entry may be either a file or a directory", prompt)
        self.assertIn("create that file", prompt)
        self.assertNotIn(v2._OLD_SCOPE_RULE, prompt)
        self.assertNotIn(v2._OLD_FINAL_DIRECTIVE, prompt)

    def test_terminal_failure_reason_is_persisted_from_new_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            logs = root / "logs"
            failed = root / "failed"
            blocked = root / "blocked"
            for path in (logs, failed, blocked):
                path.mkdir()
            task_id = "AK_BERMET_20260817T143802Z_4F0F79"
            task = failed / f"{task_id}.md"
            task.write_text(
                f"Task-ID: {task_id}\nProject-Path: /home/agent/projects/ak-bermet\n",
                encoding="utf-8",
            )
            log = logs / f"{task_id}-01B-20260817T143814Z.log"
            log.write_text(
                "CODEX_STAGE01B_FAILED\n"
                "CodexExecutionError: BLOCKED_EMPTY_IMPLEMENTATION_DIFF: no scoped changes\n",
                encoding="utf-8",
            )
            paths = SimpleNamespace(logs=logs, failed=failed, blocked=blocked)
            v2.persist_new_terminal_reasons(paths, set())
            text = task.read_text(encoding="utf-8")
            self.assertIn("Failure-Reason:", text)
            self.assertIn("BLOCKED_EMPTY_IMPLEMENTATION_DIFF", text)

    def test_existing_terminal_reason_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp) / "task.md"
            task.write_text("Failure-Reason: original\n", encoding="utf-8")
            v2._write_terminal_reason(task, "Failure-Reason", "replacement")
            text = task.read_text(encoding="utf-8")
            self.assertEqual(text.count("Failure-Reason:"), 1)
            self.assertIn("original", text)
            self.assertNotIn("replacement", text)

    def test_control_loop_upgrades_only_exact_stage01b_script_path(self) -> None:
        root = Path("/control")
        commands = [
            (
                "stage01b",
                [
                    sys.executable,
                    "/control/orchestrator/codex_stage01b_runner.py",
                    "--once",
                ],
            ),
            ("other", ["codex_stage01b_runner.py"]),
        ]
        upgraded = control_loop_service._upgrade_codex_stage01b(root, commands)
        self.assertEqual(
            upgraded[0][1][1],
            "/control/orchestrator/codex_stage01b_runner_v2.py",
        )
        self.assertEqual(upgraded[1][1], ["codex_stage01b_runner.py"])


if __name__ == "__main__":
    unittest.main()
