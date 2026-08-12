#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import claude_runner


HOLD_CHECK = (
    "node --test --experimental-strip-types "
    "src/lib/holds-availability.test.ts"
)

MIGRATION_CHECK = (
    "node --test "
    "supabase/migrations/availability-hold-security.contract.test.mjs"
)


class CampaignMigrationChecksTests(unittest.TestCase):
    def test_exact_hold_check_is_allowlisted(self):
        self.assertEqual(
            claude_runner.ALLOWED_COMMANDS[HOLD_CHECK],
            [
                "node",
                "--test",
                "--experimental-strip-types",
                "src/lib/holds-availability.test.ts",
            ],
        )

    def test_exact_migration_contract_check_is_allowlisted(self):
        self.assertEqual(
            claude_runner.ALLOWED_COMMANDS[MIGRATION_CHECK],
            [
                "node",
                "--test",
                "supabase/migrations/"
                "availability-hold-security.contract.test.mjs",
            ],
        )

    def test_ak_bermet_campaign_requires_both_checks(self):
        registry = json.loads(
            (ROOT / "orchestrator/projects.json").read_text(
                encoding="utf-8"
            )
        )

        project = next(
            item
            for item in registry["projects"]
            if item["project_id"] == "ak-bermet"
        )

        checks = project["code_required_checks"]

        self.assertIn(HOLD_CHECK, checks)
        self.assertIn(MIGRATION_CHECK, checks)

        self.assertLess(
            checks.index(HOLD_CHECK),
            checks.index(MIGRATION_CHECK),
        )

        self.assertEqual(checks[-1], "npm run build")


if __name__ == "__main__":
    unittest.main()
