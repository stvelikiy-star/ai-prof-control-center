from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


loop = load("dirty_chain_control_loop", "orchestrator/control_loop.py")
repair = load("dirty_chain_auto_repair", "orchestrator/auto_repair_runner.py")


class Stage01BDirtyChainV2Tests(unittest.TestCase):
    def git_repo(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
        (root / "src").mkdir()
        (root / "src" / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
        return root

    def test_pipeline_uses_v2_and_dual_auto_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = loop.child_commands(root, root / "state")
            stages = [name for name, _ in commands]
            self.assertEqual(stages, [
                "auto_repair_pre", "operations", "stage_01a",
                "codex_stage_01b", "auto_repair_post", "codex",
            ])
            argv = dict(commands)["codex_stage_01b"]
            self.assertTrue(any(str(x).endswith("orchestrator/codex_stage01b_runner_v2.py") for x in argv))
            self.assertFalse(any(str(x).endswith("orchestrator/codex_stage01b_runner.py") for x in argv))

    def test_exhausted_candidate_is_backed_up_cleaned_and_returns_to_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            self.git_repo(project)
            subprocess.run(["git", "checkout", "-qb", "feature/test"], cwd=project, check=True)
            (project / "src" / "tracked.txt").write_text("candidate\n", encoding="utf-8")
            (project / "src" / "new.txt").write_text("new\n", encoding="utf-8")

            paths = repair.build_paths(root / "control", root / "state")
            task = paths.failed / "TASK.md"
            task.write_text("\n".join([
                "Task-ID: TASK",
                f"Project-Path: {project}",
                "Base-Branch: main",
                "Work-Branch: feature/test",
                "Required-Checks: python3 -m unittest",
                "Scope-Files: src",
                "Codex-Review-Attempt: 1",
                "",
            ]), encoding="utf-8")
            (paths.logs / "TASK-01B-20260828T000000Z.log").write_text(
                "CODEX_STAGE01B_FAILED\nCodexExecutionError: check failed: python3 -m unittest\n",
                encoding="utf-8",
            )

            self.assertTrue(repair.recover_check_failure(paths, task, max_cycles=1))
            self.assertEqual(repair.dirty_paths(project), set())
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=project, text=True, capture_output=True, check=True).stdout.strip()
            self.assertEqual(branch, "main")
            backups = list(paths.backups.glob("TASK-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "candidate/files/src/tracked.txt").read_text(encoding="utf-8"), "candidate\n")
            self.assertEqual((backups[0] / "candidate/files/src/new.txt").read_text(encoding="utf-8"), "new\n")

    def test_wrong_work_branch_is_never_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            self.git_repo(project)
            subprocess.run(["git", "checkout", "-qb", "feature/other"], cwd=project, check=True)
            (project / "src" / "tracked.txt").write_text("other\n", encoding="utf-8")

            paths = repair.build_paths(root / "control", root / "state")
            task = paths.failed / "TASK.md"
            task.write_text("\n".join([
                "Task-ID: TASK",
                f"Project-Path: {project}",
                "Base-Branch: main",
                "Work-Branch: feature/test",
                "Required-Checks: python3 -m unittest",
                "Scope-Files: src",
                "",
            ]), encoding="utf-8")
            (paths.logs / "TASK-01B-20260828T000000Z.log").write_text(
                "CODEX_STAGE01B_FAILED\nCodexExecutionError: check failed: python3 -m unittest\n",
                encoding="utf-8",
            )

            self.assertFalse(repair.recover_check_failure(paths, task, max_cycles=5))
            self.assertEqual((project / "src" / "tracked.txt").read_text(encoding="utf-8"), "other\n")
            self.assertEqual(list(paths.backups.glob("TASK-*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
