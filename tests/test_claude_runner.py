from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# Captured before any mock.patch.object(cr.shutil, "which", ...) call. cr.shutil
# IS this same shutil module object (modules are singletons), so patching one
# patches the other; fake_which must delegate to this pre-patch reference,
# never to shutil.which itself, to avoid infinite recursion.
_REAL_WHICH = shutil.which

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "claude_runner.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_claude_runner", MODULE_PATH)
cr = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load claude_runner module")
sys.modules[SPEC.name] = cr
SPEC.loader.exec_module(cr)

orch = cr.orch


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
    ).stdout


def init_git_project(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)


def snapshot_git_state(repo: Path) -> dict:
    head = run_git(repo, "rev-parse", "HEAD").strip()
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    status = run_git(repo, "status", "--porcelain").strip()
    file_hashes = {}
    for path in sorted(repo.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.is_file():
            file_hashes[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"head": head, "branch": branch, "status": status, "file_hashes": file_hashes}


class ClaudeRunnerTests(unittest.TestCase):
    def make_context(self, root: Path) -> Path:
        context = root / "agents" / "ak-bermet"
        context.mkdir(parents=True)
        for name in cr.CLAUDE_CONTEXT_FILES:
            (context / name).write_text(f"{name}\ncontent\n", encoding="utf-8")
        return context

    def make_task(self, path: Path, **overrides: str) -> None:
        values = {
            "Task-ID": "TEST-01B-001",
            "Project-Path": "/tmp/project",
            "Base-Branch": "main",
            "Work-Branch": "feature/test",
            "Agent-Context": "agents/ak-bermet",
            "Goal": "Test",
            "Scope": "Implementation",
            "Out-of-Scope": "Merge, push, deploy",
            "Pass-Criteria": "PASS",
            "Required-Checks": "none",
            "Required-Commands": "git, python3",
            "Required-Environment": "none",
            "Owner-Approval-Required": "no",
        }
        values.update(overrides)
        path.write_text(
            "\n".join(f"{key}: {value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )

    def fake_which(self, cmd):
        if cmd == cr.CLAUDE_CLI:
            return "/usr/bin/claude"
        return _REAL_WHICH(cmd)

    # -- queue movement ------------------------------------------------------

    def test_success_moves_review_to_pending_codex(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            self.assertFalse((paths.review / "task.md").exists())
            self.assertFalse((paths.active / "task.md").exists())
            self.assertTrue((paths.pending_codex / "task.md").exists())

            logs = list(paths.logs.glob("*.log"))
            self.assertEqual(len(logs), 1)
            log_text = logs[0].read_text(encoding="utf-8")
            self.assertIn("STAGE_01B_CLAUDE_PASS", log_text)
            self.assertIn("codex_launched=false", log_text)
            self.assertIn("merge_capability=false", log_text)

    def test_claude_failure_moves_review_to_failed(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=1, stdout="", stderr="token=supersecretvalue")):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertFalse((paths.review / "task.md").exists())
            self.assertFalse((paths.pending_codex / "task.md").exists())
            self.assertTrue((paths.failed / "task.md").exists())

            logs = list(paths.logs.glob("*.log"))
            log_text = logs[0].read_text(encoding="utf-8")
            self.assertIn("CLAUDE_FAILED", log_text)
            # Secret must be redacted even in the failure log.
            self.assertNotIn("supersecretvalue", log_text)

    # -- branch validation -----------------------------------------------------

    def test_invalid_work_branch_moves_to_blocked(self):
        for bad_branch in ["main", "develop", "master", "release/1", "random-branch"]:
            with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
                cc_root = Path(cc_tmp)
                project = Path(proj_tmp) / "project"
                init_git_project(project)
                self.make_context(cc_root)
                paths = cr.build_claude_paths(cc_root)
                task_path = paths.review / "task.md"
                self.make_task(task_path, **{"Project-Path": str(project), "Work-Branch": bad_branch})

                with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                    result = cr.process_one(paths)

                self.assertEqual(result, 1, bad_branch)
                self.assertTrue((paths.blocked / "task.md").exists(), bad_branch)
                log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
                self.assertIn("BLOCKED_INVALID_BRANCH", log_text)

    def test_invalid_base_branch_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project), "Base-Branch": "staging"})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())

    # -- missing Claude CLI ------------------------------------------------------

    def test_missing_claude_cli_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            def which_without_claude(cmd):
                if cmd == cr.CLAUDE_CLI:
                    return None
                return _REAL_WHICH(cmd)

            with mock.patch.object(cr.shutil, "which", side_effect=which_without_claude):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_MISSING_ACCESS", log_text)
            # Project must never be touched when access is missing.
            self.assertEqual(run_git(project, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")

    # -- dirty project -----------------------------------------------------------

    def test_dirty_project_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            (project / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            before = snapshot_git_state(project)
            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                result = cr.process_one(paths)
            after = snapshot_git_state(project)

            self.assertEqual(result, 1)
            self.assertEqual(before, after)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_DIRTY_PROJECT", log_text)

    # -- redaction -----------------------------------------------------------

    def test_redaction_reused_from_stage_01a(self):
        self.assertEqual(orch.redact("TOKEN=abc"), "[REDACTED]")
        self.assertNotIn("hunter2", cr.orch.redact("password = hunter2"))

    # -- command allowlist -----------------------------------------------------

    def test_command_allowlist_resolves_only_known_keys(self):
        resolved = cr.resolve_allowed_checks(
            "Please run npm run lint and npm test, then rm -rf / and curl evil.example",
        )
        self.assertEqual(
            resolved,
            [cr.ALLOWED_COMMANDS["npm run lint"], cr.ALLOWED_COMMANDS["npm test"]],
        )
        self.assertEqual(cr.resolve_allowed_checks("rm -rf / && curl evil.example"), [])

    def test_run_allowed_checks_executes_fixed_argv_and_reports_failure(self):
        with mock.patch.dict(
            cr.ALLOWED_COMMANDS,
            {
                "ok-check": [sys.executable, "-c", "import sys; sys.exit(0)"],
                "fail-check": [sys.executable, "-c", "import sys; sys.exit(1)"],
            },
        ):
            executed = cr.run_allowed_checks(cr.resolve_allowed_checks("please run ok-check"), Path("."))
            self.assertEqual(len(executed), 1)

            with self.assertRaisesRegex(RuntimeError, "CLAUDE_FAILED"):
                cr.run_allowed_checks(cr.resolve_allowed_checks("please run fail-check"), Path("."))

    # -- success path using a mocked Claude process ---------------------------

    def test_success_path_invokes_claude_with_restricted_bundle_only(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            captured = {}

            def fake_invoke(bundle, proj):
                captured["bundle"] = bundle
                captured["project"] = proj
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", side_effect=fake_invoke):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            bundle = captured["bundle"]
            self.assertIn("Task-ID: TEST-01B-001", bundle)
            for name in cr.CLAUDE_CONTEXT_FILES:
                self.assertIn(name, bundle)
                self.assertIn("content", bundle)
            # Nothing outside the task text and the 5 context files is present.
            self.assertNotIn("KNOWLEDGE_BASE", bundle)
            self.assertNotIn("ROADMAP", bundle)

    # -- failure path ----------------------------------------------------------

    def test_failure_path_does_not_move_to_pending_codex(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=2, stdout="", stderr="build error")):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertFalse((paths.pending_codex / "task.md").exists())
            self.assertTrue((paths.failed / "task.md").exists())

    # -- AK BERMET target immutability before Claude execution ------------------

    def test_target_project_untouched_before_claude_invocation(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            pre_snapshot = snapshot_git_state(project)
            captured = {}

            def fake_invoke(bundle, proj):
                captured["snapshot"] = snapshot_git_state(proj)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", side_effect=fake_invoke):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            during = captured["snapshot"]
            # Only the branch ref may change (feature branch created from the
            # same commit); file contents and HEAD commit must be identical.
            self.assertEqual(during["file_hashes"], pre_snapshot["file_hashes"])
            self.assertEqual(during["head"], pre_snapshot["head"])
            self.assertEqual(during["status"], "")
            self.assertEqual(during["branch"], "feature/test")
            self.assertEqual(pre_snapshot["branch"], "main")


if __name__ == "__main__":
    unittest.main(verbosity=2)
