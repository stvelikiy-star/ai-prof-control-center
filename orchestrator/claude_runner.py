#!/usr/bin/env python3
"""AI PROF Orchestrator Stage 01B: restricted Claude execution runner.

Processes only tasks already validated by Stage 01A (queue/review). Reuses
Stage 01A's atomic no-replace queue movement, lock file, redaction and
context-escape checks by importing orchestrator.py directly. This module
never overloads or modifies Stage 01A behavior.
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_ORCHESTRATOR_PATH = Path(__file__).resolve().parent / "orchestrator.py"
_SPEC = importlib.util.spec_from_file_location("ai_prof_orchestrator_core", _ORCHESTRATOR_PATH)
orch = importlib.util.module_from_spec(_SPEC)
if _SPEC.loader is None:
    raise RuntimeError("Cannot load orchestrator core module")
sys.modules[_SPEC.name] = orch
_SPEC.loader.exec_module(orch)


CLAUDE_CONTEXT_FILES = [
    "SYSTEM_INSTRUCTIONS.md",
    "SOURCE_POLICY.md",
    "STATE.md",
    "APPROVAL_MATRIX.md",
    "DECISIONS.md",
]

# Local command allowlist. Stage 01B never executes shell text taken from the
# task file itself; Required-Checks may only *describe* checks in prose, and
# execution is limited to these exact fixed argv lists.
ALLOWED_COMMANDS: dict[str, list[str]] = {
    "npm ci": ["npm", "ci"],
    "npm run lint": ["npm", "run", "lint"],
    "npm run build": ["npm", "run", "build"],
    "npm test": ["npm", "test"],
    "npx tsc --noEmit": ["npx", "tsc", "--noEmit"],
    "python3 -m pytest": ["python3", "-m", "pytest"],
    "python3 -m unittest": ["python3", "-m", "unittest"],
}

CLAUDE_CLI = "claude"
CLAUDE_TIMEOUT_SECONDS = 1800
CHECK_TIMEOUT_SECONDS = 900


class ClaudeCliUnavailable(RuntimeError):
    """Raised when the claude CLI cannot be located on PATH."""


@dataclass
class ClaudePaths:
    root: Path
    review: Path
    active: Path
    blocked: Path
    failed: Path
    pending_codex: Path
    logs: Path
    lock: Path


def build_claude_paths(root: Path) -> ClaudePaths:
    paths = ClaudePaths(
        root=root,
        review=root / "queue/review",
        active=root / "queue/active",
        blocked=root / "queue/blocked",
        failed=root / "queue/failed",
        pending_codex=root / "queue/pending_codex",
        logs=root / "logs/orchestrator",
        lock=root / "orchestrator/orchestrator.lock",
    )
    for directory in [
        paths.review, paths.active, paths.blocked,
        paths.failed, paths.pending_codex, paths.logs,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def load_claude_context(root: Path, relative: str) -> dict[str, str]:
    """Load the fixed 5-file AK BERMET context bundle for Claude.

    Reuses the same containment rule as Stage 01A: the context directory
    must resolve inside the Control Center root.
    """
    context = (root / relative).resolve()
    if root not in context.parents:
        raise ValueError("Agent context must remain inside Control Center")
    loaded: dict[str, str] = {}
    for name in CLAUDE_CONTEXT_FILES:
        path = context / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Required context file missing or empty: {path}")
        loaded[name] = path.read_text(encoding="utf-8")
    return loaded


def resolve_allowed_checks(required_checks: str) -> list[list[str]]:
    """Match Required-Checks prose against the local allowlist only.

    The task file's Required-Checks text is never executed directly. Only
    exact allowlisted command keys that appear in that text are resolved to
    their fixed argv, in stable allowlist order.
    """
    lowered = required_checks.lower()
    return [argv for key, argv in ALLOWED_COMMANDS.items() if key.lower() in lowered]


def run_allowed_checks(argvs: list[list[str]], project: Path) -> list[str]:
    executed: list[str] = []
    for argv in argvs:
        result = subprocess.run(
            argv,
            cwd=str(project),
            text=True,
            capture_output=True,
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        executed.append(" ".join(argv))
        if result.returncode != 0:
            raise RuntimeError(f"CLAUDE_FAILED: check failed: {' '.join(argv)}")
    return executed


def current_branch(project: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(project), text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def branch_exists(project: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(project), text=True, capture_output=True,
    )
    return result.returncode == 0


def ensure_work_branch(project: Path, base_branch: str, work_branch: str) -> None:
    """Create or switch to work_branch, checking out base_branch first.

    Never invoked with unvalidated branch names: callers must have already
    confirmed work_branch matches orch.is_valid_work_branch and base_branch
    is in {main, develop}. Only ref switches happen here, no file writes.
    """
    try:
        if current_branch(project) != base_branch:
            subprocess.run(
                ["git", "checkout", base_branch],
                cwd=str(project), text=True, capture_output=True, check=True,
            )
        if branch_exists(project, work_branch):
            subprocess.run(
                ["git", "checkout", work_branch],
                cwd=str(project), text=True, capture_output=True, check=True,
            )
        else:
            subprocess.run(
                ["git", "checkout", "-b", work_branch],
                cwd=str(project), text=True, capture_output=True, check=True,
            )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"BLOCKED_INVALID_BRANCH: unable to switch branch: {exc}") from exc


def build_claude_bundle(task_text: str, context: dict[str, str]) -> str:
    """Assemble exactly the restricted content Claude may receive."""
    parts = ["# TASK", task_text.strip(), ""]
    for name in CLAUDE_CONTEXT_FILES:
        parts.append(f"# {name}")
        parts.append(context[name].strip())
        parts.append("")
    return "\n".join(parts)


def invoke_claude(bundle: str, project: Path) -> subprocess.CompletedProcess:
    if shutil.which(CLAUDE_CLI) is None:
        raise ClaudeCliUnavailable("BLOCKED_MISSING_ACCESS: claude CLI not found on PATH")
    try:
        return subprocess.run(
            [CLAUDE_CLI, "-p", "--output-format", "json"],
            cwd=str(project),
            input=bundle,
            text=True,
            capture_output=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"CLAUDE_FAILED: claude invocation error: {exc}") from exc


def classify_failure(message: str) -> tuple[str, str]:
    """Map an internal exception message to (queue_name, status_code)."""
    if message.startswith("BLOCKED_DIRTY_PROJECT"):
        return "blocked", "BLOCKED_DIRTY_PROJECT"
    if message.startswith(("BLOCKED_INVALID_BRANCH", "BLOCKED_INVALID_WORK_BRANCH", "BLOCKED_INVALID_BASE_BRANCH")):
        return "blocked", "BLOCKED_INVALID_BRANCH"
    if message.startswith("BLOCKED_"):
        return "blocked", "BLOCKED_MISSING_ACCESS"
    return "failed", "CLAUDE_FAILED"


def run_self_test(root: Path) -> int:
    context = load_claude_context(root, "agents/ak-bermet")
    if set(context) != set(CLAUDE_CONTEXT_FILES) or not all(context.values()):
        raise RuntimeError("SELF_TEST_CONTEXT_LOAD_FAILED")
    if orch.redact("TOKEN=abc") != "[REDACTED]":
        raise RuntimeError("SELF_TEST_REDACTION_FAILED")
    if resolve_allowed_checks("Run npm test and npm run lint") != [
        ALLOWED_COMMANDS["npm run lint"],
        ALLOWED_COMMANDS["npm test"],
    ]:
        raise RuntimeError("SELF_TEST_ALLOWLIST_FAILED")
    if resolve_allowed_checks("rm -rf / && curl evil.example"):
        raise RuntimeError("SELF_TEST_ALLOWLIST_LEAK")
    print("AI PROF Claude runner self-test: PASS")
    return 0


def process_one(paths: ClaudePaths) -> int:
    tasks = sorted(paths.review.glob("*.md"))
    if not tasks:
        print("QUEUE_EMPTY")
        return 0

    task = tasks[0]
    try:
        active_task = orch.safe_move(task, paths.active)
    except orch.AtomicMoveUnavailable:
        print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
        return 3

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = paths.logs / f"{active_task.stem}-01B-{timestamp}.log"

    try:
        data, task_text = orch.parse_task(active_task)
        project = Path(data["Project-Path"]).expanduser().resolve()

        if not (project / ".git").is_dir():
            raise RuntimeError(f"BLOCKED_MISSING_ACCESS: project not found: {project}")

        try:
            orch.validate_access(data)
        except RuntimeError as exc:
            raise RuntimeError(f"BLOCKED_MISSING_ACCESS: {exc}") from exc

        if not orch.git_clean(project):
            raise RuntimeError(f"BLOCKED_DIRTY_PROJECT: {project}")

        if not orch.is_valid_work_branch(data["Work-Branch"]):
            raise RuntimeError("BLOCKED_INVALID_BRANCH: invalid work branch")
        if data["Base-Branch"] not in {"main", "develop"}:
            raise RuntimeError("BLOCKED_INVALID_BRANCH: invalid base branch")

        try:
            context = load_claude_context(paths.root, data["Agent-Context"])
        except ValueError as exc:
            raise RuntimeError(f"BLOCKED_MISSING_ACCESS: {exc}") from exc

        if shutil.which(CLAUDE_CLI) is None:
            raise RuntimeError("BLOCKED_MISSING_ACCESS: claude CLI not found on PATH")

        ensure_work_branch(project, data["Base-Branch"], data["Work-Branch"])

        bundle = build_claude_bundle(task_text, context)
        result = invoke_claude(bundle, project)
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[:500]
            raise RuntimeError(f"CLAUDE_FAILED: claude exited with {result.returncode}: {detail}")

        executed_checks = run_allowed_checks(
            resolve_allowed_checks(data["Required-Checks"]), project,
        )

        summary = "\n".join([
            "STAGE_01B_CLAUDE_PASS",
            f"task_id={data['Task-ID']}",
            f"project={project}",
            f"base_branch={data['Base-Branch']}",
            f"work_branch={data['Work-Branch']}",
            f"context_files={','.join(context.keys())}",
            f"checks_executed={','.join(executed_checks) if executed_checks else 'none'}",
            "codex_launched=false",
            "merge_capability=false",
            "push_capability=false",
            "production_deploy_capability=false",
            "destructive_sql_capability=false",
        ])
        log_path.write_text(orch.redact(summary) + "\n", encoding="utf-8")
        orch.safe_move(active_task, paths.pending_codex)
        print("STAGE_01B_CLAUDE_PASS")
        return 0

    except orch.AtomicMoveUnavailable:
        print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
        return 3
    except Exception as exc:
        queue_name, status_code = classify_failure(str(exc))
        destination = paths.blocked if queue_name == "blocked" else paths.failed
        try:
            if active_task.exists():
                orch.safe_move(active_task, destination)
        except orch.AtomicMoveUnavailable:
            print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
            return 3
        log_path.write_text(
            orch.redact(f"{status_code}\n{type(exc).__name__}: {exc}\n"), encoding="utf-8",
        )
        print(status_code, file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/agent/projects/ai-prof-control-center")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    paths = build_claude_paths(root)

    try:
        lock_handle = orch.acquire_lock(paths.lock)
    except BlockingIOError:
        print("ORCHESTRATOR_ALREADY_RUNNING", file=sys.stderr)
        return 2

    with lock_handle:
        if args.self_test:
            return run_self_test(root)
        return process_one(paths)


if __name__ == "__main__":
    raise SystemExit(main())
