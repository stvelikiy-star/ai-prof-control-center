#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_paths import DEFAULT_STATE_ROOT, initialize


REQUIRED_FIELDS = [
    "Task-ID",
    "Project-Path",
    "Base-Branch",
    "Work-Branch",
    "Agent-Context",
    "Goal",
    "Scope",
    "Out-of-Scope",
    "Pass-Criteria",
    "Required-Checks",
    "Required-Commands",
    "Required-Environment",
    "Owner-Approval-Required",
]

SECRET_PATTERNS = [
    re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*=\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
]

WORK_BRANCH_PATTERN = re.compile(r"(feature|fix)/[A-Za-z0-9._/-]+")

AT_FDCWD = -100
RENAME_NOREPLACE = 1


class AtomicMoveUnavailable(RuntimeError):
    """Raised when the kernel/libc does not support atomic no-replace rename."""


@dataclass
class Paths:
    root: Path
    pending: Path
    active: Path
    review: Path
    failed: Path
    completed: Path
    blocked: Path
    logs: Path
    lock: Path


def build_paths(root: Path, state_root: Path | str | None = None) -> Paths:
    runtime = initialize(root if state_root is None else state_root)
    paths = Paths(
        root=root,
        pending=runtime / "queue/pending",
        active=runtime / "queue/active",
        review=runtime / "queue/review",
        failed=runtime / "queue/failed",
        completed=runtime / "queue/completed",
        blocked=runtime / "queue/blocked",
        logs=runtime / "logs/orchestrator",
        lock=runtime / "run/orchestrator.lock",
    )
    for directory in [
        paths.pending, paths.active, paths.review, paths.failed,
        paths.completed, paths.blocked, paths.logs,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def parse_task(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        match = re.search(rf"(?mi)^\s*{re.escape(field)}:\s*(.+?)\s*$", text)
        if match:
            values[field] = match.group(1).strip()
    missing = [field for field in REQUIRED_FIELDS if not values.get(field)]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    if values["Owner-Approval-Required"].lower() not in {"yes", "no"}:
        raise ValueError("Owner-Approval-Required must be yes or no")
    return values, text


def git_clean(project: Path) -> bool:
    import subprocess
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(project),
        text=True,
        capture_output=True,
        check=True,
    )
    return not result.stdout.strip()


def is_valid_work_branch(value: str) -> bool:
    return bool(WORK_BRANCH_PATTERN.fullmatch(value))


def _get_renameat2():
    libc = ctypes.CDLL(None, use_errno=True)
    return getattr(libc, "renameat2", None)


def safe_move(task: Path, target_dir: Path) -> Path:
    """Move a task using renameat2(RENAME_NOREPLACE) only.

    There is no non-atomic fallback: if the kernel/libc does not expose
    renameat2, this raises AtomicMoveUnavailable so the caller can stop
    safely instead of risking a non-atomic move.
    """
    target = target_dir / task.name
    target_dir.mkdir(parents=True, exist_ok=True)

    renameat2 = _get_renameat2()
    if renameat2 is None:
        raise AtomicMoveUnavailable("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE")

    renameat2.restype = ctypes.c_int
    renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]

    result = renameat2(
        AT_FDCWD,
        os.fsencode(str(task)),
        AT_FDCWD,
        os.fsencode(str(target)),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return target

    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(f"Destination task already exists: {target}")
    if error in (errno.ENOSYS, errno.EINVAL):
        raise AtomicMoveUnavailable("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE")
    raise OSError(error, os.strerror(error), str(target))


def split_csv(value: str) -> list[str]:
    if value.strip().lower() in {"none", "-"}:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def load_context(root: Path, relative: str) -> dict[str, str]:
    context = (root / relative).resolve()
    if root not in context.parents:
        raise ValueError("Agent context must remain inside Control Center")
    required = ["SYSTEM_INSTRUCTIONS.md", "SOURCE_POLICY.md", "STATE.md"]
    loaded: dict[str, str] = {}
    for name in required:
        path = context / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Required context file missing or empty: {path}")
        loaded[name] = path.read_text(encoding="utf-8")
    return loaded


def validate_access(data: dict[str, str]) -> None:
    missing_commands = [cmd for cmd in split_csv(data["Required-Commands"]) if shutil.which(cmd) is None]
    if missing_commands:
        raise RuntimeError("BLOCKED_MISSING_COMMANDS: " + ", ".join(missing_commands))

    missing_env = [name for name in split_csv(data["Required-Environment"]) if not os.environ.get(name)]
    if missing_env:
        raise RuntimeError("BLOCKED_MISSING_ENVIRONMENT: " + ", ".join(missing_env))


def acquire_lock(lock_path: Path):
    """Acquire the orchestrator's exclusive non-blocking lock.

    Raises BlockingIOError if another orchestrator instance already holds it.
    """
    handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise
    return handle


def run_self_test(root: Path) -> int:
    context = load_context(root, "agents/ak-bermet")
    if not all(context.values()):
        raise RuntimeError("SELF_TEST_CONTEXT_LOAD_FAILED")
    if redact("TOKEN=abc") != "[REDACTED]":
        raise RuntimeError("SELF_TEST_REDACTION_FAILED")
    print("AI PROF orchestrator self-test: PASS")
    return 0


def process_one(paths: Paths) -> int:
    tasks = sorted(paths.pending.glob("*.md"))
    if not tasks:
        print("QUEUE_EMPTY")
        return 0

    task = tasks[0]
    try:
        active_task = safe_move(task, paths.active)
    except AtomicMoveUnavailable:
        print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
        return 3

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = paths.logs / f"{active_task.stem}-{timestamp}.log"

    try:
        data, task_text = parse_task(active_task)
        project = Path(data["Project-Path"]).expanduser().resolve()

        if not (project / ".git").is_dir():
            raise RuntimeError(f"BLOCKED_PROJECT_NOT_FOUND: {project}")
        if not git_clean(project):
            raise RuntimeError(f"BLOCKED_DIRTY_PROJECT: {project}")
        if not is_valid_work_branch(data["Work-Branch"]):
            raise RuntimeError("BLOCKED_INVALID_WORK_BRANCH")
        if data["Base-Branch"] not in {"main", "develop"}:
            raise RuntimeError("BLOCKED_INVALID_BASE_BRANCH")

        validate_access(data)
        context = load_context(paths.root, data["Agent-Context"])

        # Stage 01A intentionally performs no target-project write and launches no agent.
        summary = "\n".join([
            "STAGE_01A_VALIDATION_PASS",
            f"task_id={data['Task-ID']}",
            f"project={project}",
            f"base_branch={data['Base-Branch']}",
            f"work_branch={data['Work-Branch']}",
            f"context_files={','.join(context.keys())}",
            "target_project_modified=false",
            "claude_launched=false",
            "merge_capability=false",
            "production_deploy_capability=false",
        ])
        log_path.write_text(redact(summary) + "\n", encoding="utf-8")
        safe_move(active_task, paths.review)
        print("STAGE_01A_VALIDATION_PASS")
        return 0

    except AtomicMoveUnavailable:
        print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
        return 3
    except Exception as exc:
        destination = paths.blocked if "BLOCKED_" in str(exc) else paths.failed
        try:
            if active_task.exists():
                safe_move(active_task, destination)
        except AtomicMoveUnavailable:
            print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
            return 3
        log_path.write_text(redact(f"ERROR\n{type(exc).__name__}: {exc}\n"), encoding="utf-8")
        print(f"ORCHESTRATOR_STOPPED: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/agent/projects/ai-prof-control-center")
    parser.add_argument("--state-root", default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    paths = build_paths(root, args.state_root)

    try:
        lock_handle = acquire_lock(paths.lock)
    except BlockingIOError:
        print("ORCHESTRATOR_ALREADY_RUNNING", file=sys.stderr)
        return 2

    with lock_handle:
        if args.self_test:
            return run_self_test(root)
        return process_one(paths)


if __name__ == "__main__":
    raise SystemExit(main())
