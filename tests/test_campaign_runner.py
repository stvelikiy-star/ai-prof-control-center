from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from orchestrator import campaign_runner as campaign


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
    ).stdout.strip()


class CampaignRunnerTests(unittest.TestCase):
    def fixture(self, parent: Path):
        root, state, project = parent / "control", parent / "state", parent / "project"
        (root / "orchestrator").mkdir(parents=True)
        (root / "agents/ak-bermet").mkdir(parents=True)
        project.mkdir()
        run_git(project, "init", "-q", "-b", "develop")
        run_git(project, "config", "user.email", "test@example.invalid")
        run_git(project, "config", "user.name", "Test")
        (project / "src").mkdir()
        (project / "src/app.txt").write_text("base\n", encoding="utf-8")
        run_git(project, "add", ".")
        run_git(project, "commit", "-qm", "base")
        run_git(project, "branch", "main")
        run_git(project, "branch", "integration/ak-bermet-3day")
        profile = {
            "project_id": "ak-bermet", "path": str(project), "base_branch": "develop",
            "allowed_base_branches": ["develop", "integration/ak-bermet-3day"],
            "local_integration_branches": ["integration/ak-bermet-3day"],
            "allow_local_campaign_merge": True,
            "work_prefixes": ["feature/", "fix/"], "allowed_scope": ["src/**"],
            "agent_context": "agents/ak-bermet", "allow_commits": False,
            "allow_push": False, "allow_merge": False, "allow_deployment": False,
        }
        (root / "orchestrator/projects.json").write_text(
            json.dumps({"version": 1, "projects": [profile]}), encoding="utf-8",
        )
        plan = parent / "plan.json"
        plan.write_text(json.dumps({
            "version": 1, "tasks": [
                {"key": "one", "title": "One", "instructions": "Change one", "scope": ["src"]},
                {"key": "two", "title": "Two", "instructions": "Change two", "scope": ["src"]},
            ],
        }), encoding="utf-8")
        return root, state, project, plan

    def start(self, root, state, plan, **kwargs):
        return campaign.start_campaign(
            root, state, "three-day", "ak-bermet", "integration/ak-bermet-3day",
            72, plan, kwargs.get("token", "owner-token"),
            now=kwargs.get("now", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )

    def approve(self, state: Path, project: Path, campaign_state: dict, *, change=True):
        task_id = campaign_state["current_task_id"]
        pending = state / "queue/pending" / f"{task_id}.md"
        approved = state / "queue/approved" / pending.name
        approved.parent.mkdir(parents=True, exist_ok=True)
        pending.replace(approved)
        work = campaign.field(approved.read_text(encoding="utf-8"), "Work-Branch")
        run_git(project, "checkout", "-qb", work, "integration/ak-bermet-3day")
        if change:
            (project / "src/app.txt").write_text("changed\n", encoding="utf-8")
        logs = state / "logs/orchestrator"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"{task_id}-01C-20260101T000000Z.log").write_text(
            "STAGE_01C_AUDIT_PASS\n", encoding="utf-8",
        )

    def test_start_is_idempotent_and_conflicts_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, _project, plan = self.fixture(Path(tmp))
            first = self.start(root, state, plan)
            second = self.start(root, state, plan)
            self.assertEqual(first["current_task_id"], second["current_task_id"])
            self.assertEqual(len(list((state / "queue/pending").glob("*.md"))), 1)
            with self.assertRaisesRegex(campaign.CampaignError, "conflicting"):
                campaign.start_campaign(
                    root, state, "three-day", "ak-bermet",
                    "integration/ak-bermet-3day", 48, plan, "owner-token",
                )

    def test_owner_approval_is_required_and_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, project, plan = self.fixture(Path(tmp))
            with self.assertRaisesRegex(campaign.CampaignError, "required"):
                self.start(root, state, plan, token="")
            started = self.start(root, state, plan)
            self.approve(state, project, started)
            approved = state / "queue/approved" / f"{started['current_task_id']}.md"
            approved.write_text(
                approved.read_text(encoding="utf-8").replace("owner-token", "wrong"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(campaign.CampaignError, "metadata"):
                campaign.tick_campaign(root, state, "three-day")

    def test_approved_task_merges_locally_and_preserves_main_develop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, project, plan = self.fixture(Path(tmp))
            main = run_git(project, "rev-parse", "main")
            develop = run_git(project, "rev-parse", "develop")
            started = self.start(root, state, plan)
            self.approve(state, project, started)
            with mock.patch.object(campaign.subprocess, "run", wraps=subprocess.run) as invoked:
                result = campaign.tick_campaign(
                    root, state, "three-day",
                    now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
                )
            self.assertEqual(result["completed_steps"], 1)
            self.assertTrue((state / "queue/completed" / f"{started['current_task_id']}.md").exists())
            self.assertEqual(run_git(project, "branch", "--show-current"), "integration/ak-bermet-3day")
            self.assertEqual(run_git(project, "rev-parse", "main"), main)
            self.assertEqual(run_git(project, "rev-parse", "develop"), develop)
            self.assertFalse(any(call.args[0][1:2] == ["push"] for call in invoked.call_args_list))
            next_task = state / "queue/pending" / f"{result['current_task_id']}.md"
            self.assertIn("Base-Branch: integration/ak-bermet-3day", next_task.read_text())

    def test_no_change_completes_without_empty_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, project, plan = self.fixture(Path(tmp))
            started = self.start(root, state, plan)
            before = run_git(project, "rev-list", "--count", "--all")
            self.approve(state, project, started, change=False)
            result = campaign.tick_campaign(root, state, "three-day")
            self.assertEqual(result["evidence"][0]["feature_commit"], "none")
            self.assertEqual(run_git(project, "rev-list", "--count", "--all"), before)

    def test_pass_evidence_and_approved_state_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, project, plan = self.fixture(Path(tmp))
            started = self.start(root, state, plan)
            task = state / "queue/pending" / f"{started['current_task_id']}.md"
            review = state / "queue/review" / task.name
            review.parent.mkdir(parents=True)
            task.replace(review)
            self.assertEqual(campaign.tick_campaign(root, state, "three-day")["completed_steps"], 0)
            approved = state / "queue/approved" / review.name
            approved.parent.mkdir(parents=True)
            review.replace(approved)
            work = campaign.field(approved.read_text(), "Work-Branch")
            run_git(project, "checkout", "-qb", work, "integration/ak-bermet-3day")
            with self.assertRaisesRegex(campaign.CampaignError, "PASS evidence"):
                campaign.tick_campaign(root, state, "three-day")

    def test_deadline_blocks_new_task_but_active_task_may_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, project, plan = self.fixture(Path(tmp))
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            started = self.start(root, state, plan, now=start)
            self.approve(state, project, started, change=False)
            result = campaign.tick_campaign(
                root, state, "three-day", now=start + timedelta(hours=73),
            )
            self.assertEqual(result["completed_steps"], 1)
            self.assertEqual(result["state"], "deadline_reached")
            self.assertIsNone(result["current_task_id"])

    def test_lock_prevents_concurrent_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, _project, plan = self.fixture(Path(tmp))
            self.start(root, state, plan)
            lock = campaign.acquire_lock(state, "three-day")
            try:
                with self.assertRaisesRegex(campaign.CampaignError, "already running"):
                    campaign.tick_campaign(root, state, "three-day")
            finally:
                lock.close()

    def test_normal_approved_task_is_never_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state, _project, _plan = self.fixture(Path(tmp))
            approved = state / "queue/approved/normal.md"
            approved.parent.mkdir(parents=True)
            approved.write_text("Task-ID: NORMAL_001\n", encoding="utf-8")
            self.assertEqual(campaign.tick_all(root, state), 0)
            self.assertTrue(approved.exists())



class CampaignLocalMigrationScopeTests(unittest.TestCase):
    def test_only_root_supabase_migrations_is_allowed(self):
        allowed = (
            "supabase/migrations",
            "supabase/migrations/20260804000100_fix.sql",
            "supabase/migrations/hold.contract.test.mjs",
        )
        for value in allowed:
            with self.subTest(value=value):
                self.assertFalse(
                    campaign.campaign_path_is_forbidden(value)
                )

    def test_other_database_and_sensitive_paths_remain_forbidden(self):
        forbidden = (
            "supabase",
            "supabase/functions/task.ts",
            "supabase/.temp/config",
            "migrations/file.sql",
            "src/migrations/file.sql",
            "other/supabase/migrations/file.sql",
            "supabase/migrations/secrets/file.sql",
            "supabase/migrations/.env",
            "../supabase/migrations/file.sql",
            "/supabase/migrations/file.sql",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertTrue(
                    campaign.campaign_path_is_forbidden(value)
                )

    def test_plan_accepts_local_migration_scope_only(self):
        import json
        import tempfile
        from pathlib import Path

        task = {
            "key": "g00b",
            "title": "Local corrective migration",
            "instructions": "Prepare and test local SQL only.",
            "scope": ["supabase/migrations"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"

            plan.write_text(
                json.dumps({"version": 1, "tasks": [task]}),
                encoding="utf-8",
            )
            loaded = campaign.load_plan(plan)
            self.assertEqual(
                loaded["tasks"][0]["scope"],
                ["supabase/migrations"],
            )

            task["scope"] = ["supabase/functions"]
            plan.write_text(
                json.dumps({"version": 1, "tasks": [task]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                campaign.CampaignError,
                "forbidden path",
            ):
                campaign.load_plan(plan)

if __name__ == "__main__":
    unittest.main()
