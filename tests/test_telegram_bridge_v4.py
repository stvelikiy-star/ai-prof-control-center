#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import telegram_bridge as legacy
import telegram_bridge_v4 as v4


class TelegramBridgeV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = {
            "allowed_scope": [
                "README.md",
                "docs/**",
                "src/**",
                "tests/**",
                "supabase/migrations/**",
            ],
            "work_prefixes": ["feature/", "fix/"],
        }

    def test_one_explicit_allowed_file_becomes_exact_scope(self) -> None:
        command = legacy.Command(
            "task",
            "ak-bermet",
            "E2E",
            "Create docs/AI_PROF_E2E_SMOKE_V3.md and nothing else",
        )
        self.assertEqual(
            v4.select_scope_v4(command, "ak-bermet", self.project),
            "docs/AI_PROF_E2E_SMOKE_V3.md",
        )

    def test_exact_scope_flows_into_submit_arguments(self) -> None:
        command = legacy.Command(
            "task",
            "ak-bermet",
            "E2E",
            "Create docs/AI_PROF_E2E_SMOKE_V3.md and nothing else",
        )
        original = legacy.select_scope
        try:
            legacy.select_scope = v4.select_scope_v4
            args, project_id = legacy.submit_arguments(
                command,
                {"ak-bermet": self.project},
            )
        finally:
            legacy.select_scope = original
        self.assertEqual(project_id, "ak-bermet")
        self.assertEqual(
            args[args.index("--scope") + 1],
            "docs/AI_PROF_E2E_SMOKE_V3.md",
        )

    def test_outside_allowlist_does_not_become_exact_scope(self) -> None:
        command = legacy.Command(
            "task",
            "ak-bermet",
            "Docs update",
            "Create secrets/private.txt for documentation",
        )
        self.assertEqual(v4.explicit_allowed_paths(command, self.project), [])
        self.assertEqual(
            v4.select_scope_v4(command, "ak-bermet", self.project),
            "docs",
        )

    def test_multiple_explicit_allowed_paths_fall_back_to_legacy_scope(self) -> None:
        command = legacy.Command(
            "task",
            "ak-bermet",
            "Docs and tests",
            "Update docs/a.md and tests/a.test.ts",
        )
        self.assertEqual(
            v4.explicit_allowed_paths(command, self.project),
            ["docs/a.md", "tests/a.test.ts"],
        )
        # One-task/one-scope contract cannot safely choose between two exact files.
        self.assertEqual(
            v4.select_scope_v4(command, "ak-bermet", self.project),
            "tests",
        )


if __name__ == "__main__":
    unittest.main()
