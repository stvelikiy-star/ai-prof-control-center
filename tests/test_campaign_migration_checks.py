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
    def test_retired_project_absent_from_registry(self):
        registry = json.loads((ROOT / "orchestrator/projects.json").read_text())
        ids = {item["project_id"] for item in registry["projects"]}
        self.assertNotIn("ak-bermet", ids)
        self.assertNotIn("ak-bermet-pilot", ids)


if __name__ == "__main__":
    unittest.main()
