from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "orchestrator.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_orchestrator", MODULE_PATH)
orch = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load orchestrator module")
sys.modules[SPEC.name] = orch
SPEC.loader.exec_module(orch)


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
    ).stdout


def init_git_project(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "nested").mkdir()
    (repo / "nested" / "file.txt").write_text("nested\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)


def snapshot_git_state(repo: Path) -> dict:
    head = run_git(repo, "rev-parse", "HEAD").strip()
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    refs = run_git(repo, "show-ref").strip()
    status = run_git(repo, "status", "--porcelain").strip()
    index_path = repo / ".git" / "index"
    index_hash = hashlib.sha256(index_path.read_bytes()).hexdigest() if index_path.exists() else None

    file_hashes = {}
    for path in sorted(repo.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.is_file():
            file_hashes[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()

    return {
        "head": head,
        "branch": branch,
        "refs": refs,
        "status": status,
        "index_hash": index_hash,
        "file_hashes": file_hashes,
    }


class OrchestratorSecurityTests(unittest.TestCase):
    def make_context(self, root: Path) -> Path:
        context = root / "agents" / "ak-bermet"
        context.mkdir(parents=True)
        for name in ("SYSTEM_INSTRUCTIONS.md", "SOURCE_POLICY.md", "STATE.md"):
            (context / name).write_text(f"{name}\ncontent\n", encoding="utf-8")
        return context

    def make_task(self, path: Path, **overrides: str) -> None:
        values = {
            "Task-ID": "TEST-001",
            "Project-Path": "/tmp/project",
            "Base-Branch": "develop",
            "Work-Branch": "feature/test",
            "Agent-Context": "agents/ak-bermet",
            "Goal": "Test",
            "Scope": "Validation",
            "Out-of-Scope": "Writes",
            "Pass-Criteria": "PASS",
            "Required-Checks": "Tests",
            "Required-Commands": "git, python3",
            "Required-Environment": "none",
            "Owner-Approval-Required": "no",
        }
        values.update(overrides)
        path.write_text(
            "\n".join(f"{key}: {value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )

    # -- safe_move: atomic no-replace behavior -----------------------------

    def test_atomic_move_rejects_existing_destination_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "pending"
            target_dir = root / "active"
            source_dir.mkdir()
            target_dir.mkdir()
            source = source_dir / "task.md"
            target = target_dir / "task.md"
            source.write_text("source", encoding="utf-8")
            target.write_text("existing", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                orch.safe_move(source, target_dir)

            self.assertEqual(source.read_text(encoding="utf-8"), "source")
            self.assertEqual(target.read_text(encoding="utf-8"), "existing")

    def test_atomic_move_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "pending"
            target_dir = root / "active"
            source_dir.mkdir()
            target_dir.mkdir()
            source = source_dir / "task.md"
            source.write_text("payload", encoding="utf-8")

            target = orch.safe_move(source, target_dir)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "payload")

    def test_renameat2_unavailable_blocks_with_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "pending"
            target_dir = root / "active"
            source_dir.mkdir()
            target_dir.mkdir()
            source = source_dir / "task.md"
            source.write_text("payload", encoding="utf-8")
            target = target_dir / "task.md"

            with mock.patch.object(orch, "_get_renameat2", return_value=None):
                with self.assertRaises(orch.AtomicMoveUnavailable):
                    orch.safe_move(source, target_dir)

            # No fallback must have executed: source untouched, no link/copy created.
            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "payload")
            self.assertFalse(target.exists())

    # -- real work-branch validator -----------------------------------------

    def test_work_branch_validator_real_function(self):
        invalid = ["main", "develop", "master", "release/1", "test/x", "feature/", "../feature/x"]
        valid = ["feature/test", "fix/security", "feature/a-b_1.2"]
        for value in invalid:
            self.assertFalse(orch.is_valid_work_branch(value), value)
        for value in valid:
            self.assertTrue(orch.is_valid_work_branch(value), value)

    def test_missing_command_and_environment_block(self):
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_MISSING_COMMANDS"):
            orch.validate_access({
                "Required-Commands": "definitely_missing_ai_prof_command",
                "Required-Environment": "none",
            })

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BLOCKED_MISSING_ENVIRONMENT"):
                orch.validate_access({
                    "Required-Commands": "python3",
                    "Required-Environment": "AI_PROF_TEST_REQUIRED_ENV",
                })

    def test_context_contents_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_context(root)
            loaded = orch.load_context(root, "agents/ak-bermet")
            self.assertEqual(set(loaded), {"SYSTEM_INSTRUCTIONS.md", "SOURCE_POLICY.md", "STATE.md"})
            self.assertTrue(all("content" in value for value in loaded.values()))

    def test_context_escape_and_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / "outside-ai-prof-test"
            outside.mkdir(exist_ok=True)
            try:
                with self.assertRaisesRegex(ValueError, "inside Control Center"):
                    orch.load_context(root, "../outside-ai-prof-test")
            finally:
                outside.rmdir()

            context = self.make_context(root)
            (context / "STATE.md").unlink()
            with self.assertRaisesRegex(ValueError, "missing or empty"):
                orch.load_context(root, "agents/ak-bermet")

    def test_redaction_variants(self):
        samples = [
            ("TOKEN=abc", "abc"),
            ("password = hunter2", "hunter2"),
            ("api_key=123456", "123456"),
            ("secret=xyz", "xyz"),
            ("sk-abcdefghijklmnopqrstuvwxyz123456", "abcdefghijklmnopqrstuvwxyz"),
        ]
        for sample, forbidden in samples:
            self.assertNotIn(forbidden, orch.redact(sample))

    # -- real lock-acquisition function --------------------------------------

    def test_lock_contention_real_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "orchestrator.lock"
            first = orch.acquire_lock(lock_path)
            try:
                with self.assertRaises(BlockingIOError):
                    orch.acquire_lock(lock_path)
            finally:
                first.close()

            # Lock is released once the handle is closed; re-acquisition then succeeds.
            second = orch.acquire_lock(lock_path)
            second.close()

    def test_dirty_git_repository_detected_without_head_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_project(repo)
            head_before = run_git(repo, "rev-parse", "HEAD").strip()

            self.assertTrue(orch.git_clean(repo))
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
            self.assertFalse(orch.git_clean(repo))

            head_after = run_git(repo, "rev-parse", "HEAD").strip()
            self.assertEqual(head_before, head_after)

    def test_task_parser_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            self.make_task(task)
            data, text = orch.parse_task(task)
            self.assertEqual(data["Work-Branch"], "feature/test")
            self.assertIn("Task-ID: TEST-001", text)

            task.write_text("Task-ID: broken\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing required fields"):
                orch.parse_task(task)

    # -- full production-path integration cycles -----------------------------

    def test_full_successful_process_one_cycle_preserves_target_project(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)

            self.make_context(cc_root)
            paths = orch.build_paths(cc_root)
            task_path = paths.pending / "task.md"
            # Base-Branch must be one of {main, develop}; pin it explicitly to the
            # repo's actual current branch so the check exercises a real match.
            current_branch = run_git(project, "rev-parse", "--abbrev-ref", "HEAD").strip()
            base_branch = current_branch if current_branch in {"main", "develop"} else "main"
            self.make_task(
                task_path,
                **{"Project-Path": str(project), "Base-Branch": base_branch},
            )

            before = snapshot_git_state(project)
            result = orch.process_one(paths)
            after = snapshot_git_state(project)

            self.assertEqual(result, 0)
            self.assertEqual(before, after)

            self.assertFalse((paths.pending / "task.md").exists())
            self.assertFalse((paths.active / "task.md").exists())
            self.assertTrue((paths.review / "task.md").exists())

            logs = list(paths.logs.glob("*.log"))
            self.assertEqual(len(logs), 1)
            log_text = logs[0].read_text(encoding="utf-8")
            self.assertIn("STAGE_01A_VALIDATION_PASS", log_text)
            self.assertIn("target_project_modified=false", log_text)
            self.assertIn("claude_launched=false", log_text)

    def test_dirty_project_cycle_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            (project / "tracked.txt").write_text("dirty\n", encoding="utf-8")

            self.make_context(cc_root)
            paths = orch.build_paths(cc_root)
            task_path = paths.pending / "task.md"
            current_branch = run_git(project, "rev-parse", "--abbrev-ref", "HEAD").strip()
            base_branch = current_branch if current_branch in {"main", "develop"} else "main"
            self.make_task(
                task_path,
                **{"Project-Path": str(project), "Base-Branch": base_branch},
            )

            before = snapshot_git_state(project)
            result = orch.process_one(paths)
            after = snapshot_git_state(project)

            self.assertEqual(result, 1)
            self.assertEqual(before, after)

            self.assertFalse((paths.pending / "task.md").exists())
            self.assertFalse((paths.active / "task.md").exists())
            self.assertFalse((paths.review / "task.md").exists())
            self.assertTrue((paths.blocked / "task.md").exists())

    def test_process_one_stops_safely_when_atomic_move_unavailable(self):
        with tempfile.TemporaryDirectory() as cc_tmp:
            cc_root = Path(cc_tmp)
            self.make_context(cc_root)
            paths = orch.build_paths(cc_root)
            task_path = paths.pending / "task.md"
            self.make_task(task_path)

            with mock.patch.object(orch, "_get_renameat2", return_value=None):
                result = orch.process_one(paths)

            self.assertEqual(result, 3)
            # Task must remain exactly where it was: no partial/non-atomic move.
            self.assertTrue(task_path.exists())
            self.assertFalse((paths.active / "task.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
