from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from orchestrator import ai_prof_approved_task_publisher_gate as gate


class AIProfCommitOnlyGateTests(unittest.TestCase):
    def setUp(self):
        self.scope = ["reports/evidence.md"]
        self.profile = {
            "project_id": gate.PROJECT_ID,
            "path": gate.PROJECT_PATH,
            "base_branch": gate.BASE_BRANCH,
            "allowed_base_branches": [gate.BASE_BRANCH],
            "agent_context": gate.AGENT_CONTEXT,
            "allowed_scope": ["reports/**"],
            "forbidden_scope": [".git/**", "queue/**"],
            "allow_commits": True,
            "allow_push": False,
            "allow_merge": False,
            "allow_deployment": False,
            "require_clean_repository": True,
        }
        self.task = {
            "task_id": "AI_PROF_CONTROL_CENTER_TEST",
            "lifecycle_state": "APPROVED",
            "project_id": gate.PROJECT_ID,
            "project_path": gate.PROJECT_PATH,
            "base_branch": gate.BASE_BRANCH,
            "work_branch": "feature/chatgpt-issue-125",
            "agent_context": gate.AGENT_CONTEXT,
            "scope_files": self.scope,
            "source": {
                "kind": "github_issue",
                "repository": gate.SOURCE_REPOSITORY,
                "issue": 125,
            },
        }
        self.repository = {
            "project_id": gate.PROJECT_ID,
            "path": gate.PROJECT_PATH,
            "base_branch": gate.BASE_BRANCH,
            "work_branch": "feature/chatgpt-issue-125",
            "base_sha": "a" * 40,
            "changed_paths": self.scope,
        }

    def evaluate(self, *, task=None, profile=None, repository=None):
        return gate.evaluate_publication_authority(
            self.task if task is None else task,
            self.profile if profile is None else profile,
            self.repository if repository is None else repository,
        )

    def test_only_commit_is_authorized(self):
        decision = self.evaluate()
        self.assertEqual(decision.decision, "COMMIT_AUTHORIZED")
        self.assertEqual(decision.reason, "commit_only_authorized")
        self.assertFalse(decision.committed)
        self.assertFalse(decision.published)
        self.assertFalse(decision.complete)
        with self.assertRaises(FrozenInstanceError):
            decision.published = True

    def test_commit_must_be_true_and_every_other_capability_false(self):
        for value in (False, None, 1, "true"):
            profile = dict(self.profile, allow_commits=value)
            self.assertEqual(
                self.evaluate(profile=profile).reason,
                "commit_capability_disabled",
            )
        for flag in gate.NON_COMMIT_CAPABILITIES:
            for value in (True, None, 0, "false"):
                with self.subTest(flag=flag, value=value):
                    profile = dict(self.profile)
                    profile[flag] = value
                    decision = self.evaluate(profile=profile)
                    self.assertEqual(decision.reason, "unexpected_capability_state")
                    self.assertFalse(decision.committed)

    def test_exact_source_branch_sha_and_scope_are_required(self):
        cases = (
            ("task", "work_branch", "feature/other-125", "wrong_branch_identity"),
            ("task", "scope_files", ["../escape"], "invalid_scope"),
            ("task", "scope_files", ["orchestrator/projects.json"], "forbidden_scope"),
            ("repository", "base_sha", "bad", "missing_base_sha"),
            ("repository", "changed_paths", ["reports/other.md"], "candidate_scope_mismatch"),
            ("repository", "path", "/tmp/other", "wrong_repository_identity"),
        )
        for target, key, value, reason in cases:
            with self.subTest(target=target, key=key):
                task = dict(self.task)
                profile = dict(self.profile)
                repository = dict(self.repository)
                {"task": task, "profile": profile, "repository": repository}[target][key] = value
                self.assertEqual(
                    gate.evaluate_publication_authority(
                        task, profile, repository
                    ).reason,
                    reason,
                )

    def test_only_one_exact_approved_task_is_selected(self):
        self.assertEqual(
            gate.evaluate_approved_tasks(
                [self.task, dict(self.task)], self.profile, self.repository
            ).reason,
            "ambiguous_approved_task",
        )
        wrong = dict(self.task, project_id="other")
        selected = gate.evaluate_approved_tasks(
            [wrong, self.task], self.profile, self.repository
        )
        self.assertEqual(selected.decision, "COMMIT_AUTHORIZED")

    def _git(self, repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True,
            stdout=subprocess.PIPE, text=True
        ).stdout.strip()

    def _repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "test@example.invalid")
        self._git(repo, "config", "user.name", "AI PROF Test")
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-m", "base")
        self._git(repo, "branch", gate.BASE_BRANCH)
        self._git(repo, "switch", "-c", self.task["work_branch"])
        base = self._git(repo, "rev-parse", "HEAD")
        return repo.resolve(), base

    def test_exact_local_commit_and_idempotent_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._repo(Path(tmp))
            (repo / "reports").mkdir()
            (repo / "reports/evidence.md").write_text("PASS\n", encoding="utf-8")
            auth = gate.CommitAuthorization(
                self.task["task_id"], self.task["work_branch"],
                base, tuple(self.scope)
            )
            commit = gate.commit_approved_change(repo, auth)
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
            self.assertEqual(self._git(repo, "rev-parse", "HEAD^"), base)
            self.assertEqual(
                self._git(repo, "log", "-1", "--format=%s"),
                gate.COMMIT_SUBJECT_PREFIX + self.task["task_id"],
            )
            self.assertEqual(self._git(repo, "status", "--porcelain"), "")
            self.assertEqual(gate.commit_approved_change(repo, auth), commit)
            self.assertEqual(self._git(repo, "rev-list", "--count", f"{base}..HEAD"), "1")

    def test_staged_partial_attempt_is_recovered_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._repo(Path(tmp))
            (repo / "reports").mkdir()
            (repo / "reports/evidence.md").write_text("PASS\n", encoding="utf-8")
            self._git(repo, "add", "reports/evidence.md")
            auth = gate.CommitAuthorization(
                self.task["task_id"], self.task["work_branch"],
                base, tuple(self.scope)
            )
            commit = gate.commit_approved_change(repo, auth)
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), commit)
            self.assertEqual(self._git(repo, "rev-list", "--count", f"{base}..HEAD"), "1")

    def test_stale_base_and_extra_path_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._repo(Path(tmp))
            (repo / "reports").mkdir()
            (repo / "reports/evidence.md").write_text("PASS\n", encoding="utf-8")
            (repo / "reports/extra.md").write_text("NO\n", encoding="utf-8")
            auth = gate.CommitAuthorization(
                self.task["task_id"], self.task["work_branch"],
                base, tuple(self.scope)
            )
            with self.assertRaisesRegex(gate.CommitBlocked, "candidate_scope_mismatch"):
                gate.commit_approved_change(repo, auth)
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), base)

            stale = gate.CommitAuthorization(
                self.task["task_id"], self.task["work_branch"],
                "b" * 40, tuple(self.scope)
            )
            with self.assertRaisesRegex(gate.CommitBlocked, "base_sha_drift"):
                gate.commit_approved_change(repo, stale)

    def test_execution_uses_no_network_publication_or_shell(self):
        source = Path(gate.__file__).read_text(encoding="utf-8")
        self.assertNotIn('["gh"', source)
        self.assertNotIn('"push"', source)
        self.assertNotIn('"merge"', source)
        self.assertNotIn("shell=True", source)
        self.assertIn('"update-ref"', source)

        with mock.patch.object(gate, "commit_approved_change") as commit:
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
                commit.return_value = "c" * 40
                decision = gate.run_once(root, state)
                self.assertEqual(decision.decision, "COMMITTED")
                self.assertFalse(decision.published)
                self.assertFalse(decision.complete)
                commit.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

