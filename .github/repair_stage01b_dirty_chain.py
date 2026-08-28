from pathlib import Path


def replace_exact(text: str, old: str, new: str, label: str, count: int = 1) -> str:
    found = text.count(old)
    if found < count:
        raise SystemExit(f"{label}: expected at least {count}, found {found}")
    return text.replace(old, new, count)


# 1. Control-loop: run repair before intake, use Stage01B V2, then repair immediately after it.
path = Path("orchestrator/control_loop.py")
text = path.read_text(encoding="utf-8")
old_auto = '        ("auto_repair", [python, str(root / "orchestrator/auto_repair_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),'
new_auto = '        ("auto_repair_pre", [python, str(root / "orchestrator/auto_repair_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),'
text = replace_exact(text, old_auto, new_auto, "control-loop auto-repair pre")
old_stage = '        ("codex_stage_01b", [python, str(root / "orchestrator/codex_stage01b_runner.py"), "--root", base, "--state-root", str(runtime)]),'
new_stage = '        ("codex_stage_01b", [python, str(root / "orchestrator/codex_stage01b_runner_v2.py"), "--root", base, "--state-root", str(runtime)]),'
text = replace_exact(text, old_stage, new_stage, "Stage01B V2 activation")
post = '        ("auto_repair_post", [python, str(root / "orchestrator/auto_repair_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),'
if post not in text:
    text = text.replace(new_stage, new_stage + "\n" + post, 1)
old_order = '["operations", "stage_01a", "auto_repair", "codex_stage_01b", "codex"]'
new_order = '["auto_repair_pre", "operations", "stage_01a", "codex_stage_01b", "auto_repair_post", "codex"]'
text = replace_exact(text, old_order, new_order, "control-loop self-test order")
path.write_text(text, encoding="utf-8")


# 2. Existing control-loop tests: update only contracts affected by the six-stage pipeline.
path = Path("tests/test_control_loop.py")
text = path.read_text(encoding="utf-8")
if text.count(old_order) != 2:
    raise SystemExit(f"test_control_loop order assertions: expected 2, found {text.count(old_order)}")
text = text.replace(old_order, new_order)
text = replace_exact(text, "side_effect=[1, 1, 1, 1, 0]", "side_effect=[1, 1, 1, 1, 1, 0]", "task failure six-stage side effect")
text = replace_exact(text, "self.assertEqual(run.call_count, 5)", "self.assertEqual(run.call_count, 6)", "task failure six-stage call count")
path.write_text(text, encoding="utf-8")


# 3. Auto-repair: directory scope, branch binding, terminal cleanup.
path = Path("orchestrator/auto_repair_runner.py")
text = path.read_text(encoding="utf-8")
insert_at = 'def git_run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:\n'
helper = '''def path_within_scope(
    project: Path,
    relative: str,
    scope: tuple[str, ...],
) -> bool:
    """Accept an exact scoped file or a child of an existing scoped directory."""
    candidate = PurePosixPath(relative)
    for value in scope:
        scoped = PurePosixPath(value)
        if candidate == scoped:
            return True
        absolute = project / value
        if absolute.is_dir() and scoped in candidate.parents:
            return True
    return False


def dirty_is_within_scope(
    project: Path,
    dirty: set[str],
    scope: tuple[str, ...],
) -> bool:
    return all(path_within_scope(project, relative, scope) for relative in dirty)


def current_branch(project: Path) -> str:
    return git_run(project, "branch", "--show-current").stdout.strip()


def candidate_branch_matches(project: Path, work_branch: str) -> bool:
    """Bind a dirty candidate to the exact task work branch."""
    return bool(work_branch) and current_branch(project) == work_branch


def restore_terminal_base_branch(
    project: Path,
    base_branch: str,
    work_branch: str,
) -> None:
    """Return an exhausted clean candidate from its work branch to base."""
    if dirty_paths(project):
        raise AutoRepairError("refusing terminal branch restore while repository is dirty")
    current = current_branch(project)
    if current == base_branch:
        return
    if current != work_branch:
        raise AutoRepairError(
            f"refusing terminal branch restore from unexpected branch: {current}"
        )
    git_run(project, "checkout", base_branch)
    actual = current_branch(project)
    if actual != base_branch:
        raise AutoRepairError(
            f"terminal branch restore did not reach base branch: {actual}"
        )


'''
if "def path_within_scope(" not in text:
    if insert_at not in text:
        raise SystemExit("auto-repair helper insertion point missing")
    text = text.replace(insert_at, helper + insert_at, 1)

old_restore = '''    scope_set = set(scope)
    if not dirty:
        raise AutoRepairError("failed Stage 01B has no dirty implementation to restore")
    outside = sorted(dirty - scope_set)
    if outside:'''
new_restore = '''    if not dirty:
        raise AutoRepairError("failed Stage 01B has no dirty implementation to restore")
    outside = sorted(
        relative
        for relative in dirty
        if not path_within_scope(project, relative, scope)
    )
    if outside:'''
text = replace_exact(text, old_restore, new_restore, "directory-aware rollback")

old_max = '''    current_attempt = review_attempt(task_text)
    next_attempt = current_attempt + 1
    if next_attempt > max_cycles:
        return False
    project_raw = field(task_text, "Project-Path")'''
new_max = '''    current_attempt = review_attempt(task_text)
    next_attempt = current_attempt + 1
    project_raw = field(task_text, "Project-Path")'''
text = replace_exact(text, old_max, new_max, "terminal-cycle decision")

old_candidate = '''    scope = parse_scope_files(task_text)
    dirty = dirty_paths(project)
    if not dirty or not dirty.issubset(set(scope)):
        return False

    backup = backup_evidence(paths, task, [log_01b])'''
new_candidate = '''    scope = parse_scope_files(task_text)

    work_branch = field(task_text, "Work-Branch")
    base_branch = field(task_text, "Base-Branch")
    if not work_branch or not base_branch:
        raise AutoRepairError("Base-Branch or Work-Branch is missing")

    # Never attribute another task's dirty diff to this failed task.
    if not candidate_branch_matches(project, work_branch):
        return False

    dirty = dirty_paths(project)
    if not dirty or not dirty_is_within_scope(project, dirty, scope):
        return False

    if next_attempt > max_cycles:
        backup = backup_evidence(paths, task, [log_01b])
        backup_failed_candidate(project, scope, backup)
        restore_task_scope_to_head(project, scope)
        restore_terminal_base_branch(project, base_branch, work_branch)

        feedback = "\\n".join([
            f"- Automatic-Repair-Cycle-Limit: {current_attempt}/{max_cycles}",
            f"- Failed-Required-Check: `{command}`",
            "- Repair-cycle limit reached; no uncontrolled retry was created.",
            "- The terminal failed candidate was preserved in the auto-repair backup.",
            "- The task scope was restored to clean HEAD.",
            f"- The project was returned to base branch `{base_branch}`.",
            "- The task remains failed and requires new evidence or an explicit later decision.",
        ])
        atomic_write(task, set_auto_feedback(task_text, feedback))
        print("AUTO_REPAIR_CHECK_FAILURE_EXHAUSTED_CLEANED")
        print(f"task_id={task_id}")
        print(f"repair_cycle={current_attempt}/{max_cycles}")
        print(f"failed_check={command}")
        print(f"backup={backup}")
        print(f"base_branch={base_branch}")
        return True

    backup = backup_evidence(paths, task, [log_01b])'''
text = replace_exact(text, old_candidate, new_candidate, "branch-bound terminal cleanup")
path.write_text(text, encoding="utf-8")


# 4. Add isolated regression coverage instead of expanding existing repair tests.
Path("tests/test_stage01b_dirty_chain_v2.py").write_text('''from __future__ import annotations

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
        (root / "src" / "tracked.txt").write_text("base\\n", encoding="utf-8")
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
            (project / "src" / "tracked.txt").write_text("candidate\\n", encoding="utf-8")
            (project / "src" / "new.txt").write_text("new\\n", encoding="utf-8")

            paths = repair.build_paths(root / "control", root / "state")
            task = paths.failed / "TASK.md"
            task.write_text("\\n".join([
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
                "CODEX_STAGE01B_FAILED\\nCodexExecutionError: check failed: python3 -m unittest\\n",
                encoding="utf-8",
            )

            self.assertTrue(repair.recover_check_failure(paths, task, max_cycles=1))
            self.assertEqual(repair.dirty_paths(project), set())
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=project, text=True, capture_output=True, check=True).stdout.strip()
            self.assertEqual(branch, "main")
            backups = list(paths.backups.glob("TASK-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "candidate/files/src/tracked.txt").read_text(encoding="utf-8"), "candidate\\n")
            self.assertEqual((backups[0] / "candidate/files/src/new.txt").read_text(encoding="utf-8"), "new\\n")

    def test_wrong_work_branch_is_never_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            self.git_repo(project)
            subprocess.run(["git", "checkout", "-qb", "feature/other"], cwd=project, check=True)
            (project / "src" / "tracked.txt").write_text("other\\n", encoding="utf-8")

            paths = repair.build_paths(root / "control", root / "state")
            task = paths.failed / "TASK.md"
            task.write_text("\\n".join([
                "Task-ID: TASK",
                f"Project-Path: {project}",
                "Base-Branch: main",
                "Work-Branch: feature/test",
                "Required-Checks: python3 -m unittest",
                "Scope-Files: src",
                "",
            ]), encoding="utf-8")
            (paths.logs / "TASK-01B-20260828T000000Z.log").write_text(
                "CODEX_STAGE01B_FAILED\\nCodexExecutionError: check failed: python3 -m unittest\\n",
                encoding="utf-8",
            )

            self.assertFalse(repair.recover_check_failure(paths, task, max_cycles=5))
            self.assertEqual((project / "src" / "tracked.txt").read_text(encoding="utf-8"), "other\\n")
            self.assertEqual(list(paths.backups.glob("TASK-*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
''', encoding="utf-8")
