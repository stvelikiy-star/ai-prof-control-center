#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import codex_stage01b_runner_v2


LEGACY_HOLD_CHECK = (
    "node --test --experimental-strip-types "
    "src/lib/holds-availability.test.ts"
)

LEGACY_MIGRATION_CHECK = (
    "node --test "
    "supabase/migrations/availability-hold-security.contract.test.mjs"
)

CURRENT_CHECKS = [
    "npm run lint",
    "npx tsc --noEmit --incremental false",
    "npm run test:inspection",
    "npm run build",
]


class CampaignMigrationChecksTests(unittest.TestCase):
    def _ak_bermet_project(self):
        registry = json.loads(
            (ROOT / "orchestrator/projects.json").read_text(encoding="utf-8")
        )
        return next(
            item
            for item in registry["projects"]
            if item["project_id"] == "ak-bermet"
        )

    def test_removed_hold_contracts_are_not_required_by_current_registry(self):
        checks = self._ak_bermet_project()["code_required_checks"]
        self.assertNotIn(LEGACY_HOLD_CHECK, checks)
        self.assertNotIn(LEGACY_MIGRATION_CHECK, checks)

    def test_ak_bermet_current_checks_are_exact_and_ordered(self):
        checks = self._ak_bermet_project()["code_required_checks"]
        self.assertEqual(checks, CURRENT_CHECKS)

    def test_every_current_ak_bermet_check_is_effective_stage01b_allowlisted(self):
        codex_stage01b_runner_v2.install_v2_required_check_allowlist()
        effective = codex_stage01b_runner_v2.legacy.core.ALLOWED_COMMANDS
        for check in CURRENT_CHECKS:
            with self.subTest(check=check):
                self.assertIn(check, effective)


if __name__ == "__main__":
    unittest.main()
