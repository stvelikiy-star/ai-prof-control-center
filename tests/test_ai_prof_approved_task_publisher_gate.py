from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from orchestrator import ai_prof_approved_task_publisher_gate as gate


class AIProfApprovedTaskPublisherGateTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "project_id": gate.PROJECT_ID,
            "path": gate.PROJECT_PATH,
            "base_branch": gate.BASE_BRANCH,
            "allowed_base_branches": [gate.BASE_BRANCH],
            "agent_context": gate.AGENT_CONTEXT,
            "allow_commits": False,
            "allow_push": False,
            "allow_merge": False,
            "allow_deployment": False,
        }
        self.task = {
            "task_id": "AI_PROF_CONTROL_CENTER_TEST",
            "lifecycle_state": "APPROVED",
            "project_id": gate.PROJECT_ID,
            "project_path": gate.PROJECT_PATH,
            "base_branch": gate.BASE_BRANCH,
            "work_branch": "feature/chatgpt-issue-116",
            "agent_context": gate.AGENT_CONTEXT,
            "source": {
                "kind": "github_issue",
                "repository": gate.SOURCE_REPOSITORY,
                "issue": 116,
            },
        }
        self.repository = {
            "project_id": gate.PROJECT_ID,
            "path": gate.PROJECT_PATH,
            "base_branch": gate.BASE_BRANCH,
            "work_branch": "feature/chatgpt-issue-116",
        }

    def evaluate(self, *, task=None, profile=None, repository=None):
        return gate.evaluate_publication_authority(
            self.task if task is None else task,
            self.profile if profile is None else profile,
            self.repository if repository is None else repository,
        )

    def test_current_false_capabilities_require_owner_without_completion(self):
        decision = self.evaluate()
        self.assertEqual(decision.decision, "OWNER_ACTION_REQUIRED")
        self.assertEqual(decision.reason, "owner_capabilities_disabled")
        self.assertEqual(decision.task_id, self.task["task_id"])
        self.assertFalse(decision.published)
        self.assertFalse(decision.complete)
        with self.assertRaises(FrozenInstanceError):
            decision.published = True

    def test_every_capability_must_be_exact_false(self):
        for capability in gate.CAPABILITY_FLAGS:
            for unexpected in (True, None, 0, "false"):
                with self.subTest(capability=capability, unexpected=unexpected):
                    profile = dict(self.profile)
                    profile[capability] = unexpected
                    decision = self.evaluate(profile=profile)
                    self.assertEqual(decision.decision, "OWNER_ACTION_REQUIRED")
                    self.assertEqual(decision.reason, "unexpected_capability_state")
                    self.assertFalse(decision.published)

    def test_exact_project_profile_repository_and_branch_are_required(self):
        changes = (
            ("task", "project_id", "other", "wrong_project"),
            ("task", "project_path", "/tmp/other", "wrong_project"),
            ("task", "base_branch", "main", "wrong_branch_identity"),
            ("task", "work_branch", "feature/other-116", "wrong_branch_identity"),
            ("task", "agent_context", "agents/other", "wrong_profile_identity"),
            ("profile", "path", "/tmp/other", "wrong_profile_identity"),
            ("profile", "project_id", "other", "wrong_profile_identity"),
            ("repository", "path", "/tmp/other", "wrong_repository_identity"),
            ("repository", "work_branch", "feature/chatgpt-issue-117", "wrong_branch_identity"),
        )
        for target, key, value, reason in changes:
            with self.subTest(target=target, key=key):
                task = dict(self.task)
                profile = dict(self.profile)
                repository = dict(self.repository)
                locals()[target][key] = value
                decision = gate.evaluate_publication_authority(
                    task, profile, repository
                )
                self.assertEqual(decision.reason, reason)
                self.assertFalse(decision.published)

    def test_source_issue_must_match_supported_work_branch(self):
        for source in (
            None,
            {},
            {"kind": "github_issue", "repository": "other/repo", "issue": 116},
            {
                "kind": "github_issue",
                "repository": gate.SOURCE_REPOSITORY,
                "issue": 117,
            },
        ):
            with self.subTest(source=source):
                task = dict(self.task)
                task["source"] = source
                decision = self.evaluate(task=task)
                self.assertEqual(decision.reason, "wrong_source_identity")

    def test_only_one_exact_approved_task_can_be_selected(self):
        self.assertEqual(
            gate.evaluate_approved_tasks(
                [self.task, dict(self.task)], self.profile, self.repository
            ).reason,
            "ambiguous_approved_task",
        )
        wrong_project = dict(self.task, project_id="other")
        selected = gate.evaluate_approved_tasks(
            [wrong_project, self.task], self.profile, self.repository
        )
        self.assertEqual(selected.reason, "owner_capabilities_disabled")
        self.assertEqual(
            gate.evaluate_approved_tasks(
                [wrong_project], self.profile, self.repository
            ).reason,
            "approved_task_not_found",
        )

    def test_missing_or_malformed_policy_denies_without_filesystem_mutation(self):
        for profile in (None, [], {}, {"project_id": gate.PROJECT_ID}):
            with self.subTest(profile=profile):
                decision = gate.evaluate_publication_authority(
                    self.task, profile, self.repository
                )
                self.assertEqual(decision.decision, "OWNER_ACTION_REQUIRED")
                self.assertFalse(decision.published)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            state = Path(tmp) / "state"
            (root / "orchestrator").mkdir(parents=True)
            (state / "validated").mkdir(parents=True)
            profile_path = root / "orchestrator/projects.json"
            envelope_path = state / "validated/ai-prof-approved-task.json"
            profile_path.write_text("not json", encoding="utf-8")
            envelope_path.write_text(
                json.dumps({"task": self.task, "repository": self.repository}),
                encoding="utf-8",
            )
            before = self._tree_snapshot(Path(tmp))
            decision = gate.run_once(root, state)
            after = self._tree_snapshot(Path(tmp))
            self.assertEqual(decision.decision, "OWNER_ACTION_REQUIRED")
            self.assertEqual(decision.reason, "policy_or_approval_metadata_unavailable")
            self.assertEqual(after, before)

    def test_valid_envelope_is_read_only_and_never_calls_a_publisher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            state = Path(tmp) / "state"
            (root / "orchestrator").mkdir(parents=True)
            (state / "validated").mkdir(parents=True)
            (root / "orchestrator/projects.json").write_text(
                json.dumps({"projects": [self.profile]}), encoding="utf-8"
            )
            (state / "validated/ai-prof-approved-task.json").write_text(
                json.dumps({"task": self.task, "repository": self.repository}),
                encoding="utf-8",
            )
            before = self._tree_snapshot(Path(tmp))
            decision = gate.run_once(root, state)
            self.assertEqual(decision.reason, "owner_capabilities_disabled")
            self.assertEqual(self._tree_snapshot(Path(tmp)), before)

        source = Path(gate.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("approved_task_publisher_gate import", source)
        self.assertNotIn("ak_bermet_approved_task_publisher_gate import", source)

    @staticmethod
    def _tree_snapshot(root: Path):
        return tuple(
            (
                path.relative_to(root).as_posix(),
                path.is_dir(),
                None if path.is_dir() else path.read_bytes(),
            )
            for path in sorted(root.rglob("*"))
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
