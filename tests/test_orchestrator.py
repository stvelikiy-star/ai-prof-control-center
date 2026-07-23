from __future__ import annotations

import fcntl
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

    def test_invalid_and_valid_work_branch_patterns(self):
        invalid = ["main", "develop", "master", "release/1", "test/x", "feature/", "../feature/x"]
        valid = ["feature/test", "fix/security", "feature/a-b_1.2"]
        pattern = orch.re.compile(r"(feature|fix)/[A-Za-z0-9._/-]+")
        for value in invalid:
            self.assertIsNone(pattern.fullmatch(value), value)
        for value in valid:
            self.assertIsNotNone(pattern.fullmatch(value), value)

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

    def test_lock_contention(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "orchestrator.lock"
            with lock_path.open("w", encoding="utf-8") as first:
                fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with lock_path.open("w", encoding="utf-8") as second:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_dirty_git_repository_detected_without_head_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            head_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

            self.assertTrue(orch.git_clean(repo))
            tracked.write_text("changed\n", encoding="utf-8")
            self.assertFalse(orch.git_clean(repo))

            head_after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
