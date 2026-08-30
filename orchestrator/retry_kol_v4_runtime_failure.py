#!/usr/bin/env python3
"""Evidence-preserving retry for one proven pre-execution KÖL V4 failure.

This is deliberately not a generic failed-task requeue command. It handles only
the exact failure class proven by the first live KÖL V4 E2E: Stage 01B rejected
the repository-owned ``npm run check:release-source`` command as unsupported
before implementation began.

Safety properties:
- Control Center must be observably paused and its supervisor lock must be free.
- The task must exist exactly once in ``failed`` and be an exact KÖL V4 task.
- The KÖL checkout must be clean on local ``main`` and equal local origin/main.
- The repaired Stage 01B V2 required-check contract must resolve exactly.
- The original failed task and Stage 01B logs are copied into a private backup.
- Publication-authority fields are preserved byte-for-byte by value.
- Only the stale terminal Failure-Reason is removed from the retry copy.
- A single Retry-Attempt marker is added; a second retry is rejected.
- The final failed -> pending transition is one same-filesystem atomic rename.

No commit, push, merge, deployment, database, secret, payment, production, or
cross-project authority is granted by this helper.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_stage01b_runner_v2 as stage01b_v2
import kol_publication_contract_v4 as kol_v4
from runtime_paths import DEFAULT_STATE_ROOT, initialize

DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
KOL_PROJECT = Path("/home/agent/Загрузки/kol-travel-platform")
KOL_BASE_BRANCH = "main"
EXPECTED_FAILURE_FRAGMENT = (
    "required checks mismatch: expected=['npm run lint', "
    "'npx tsc --noEmit --incremental false', "
    "'npm run check:release-source', 'npm run build']; "
    "reported=[]; unsupported=['npm run check:release-source']"
)
EXPECTED_REQUIRED_CHECKS = (
    "npm run lint, npx tsc --noEmit --incremental false, "
    "npm run check:release-source, npm run build"
)
CRITICAL_FIELDS = (
    "Task-ID",
    "Project-Path",
    "Base-Branch",
    "Work-Branch",
    "Goal",
    "Required-Checks",
    "Scope-Files",
    "Publication-Contract-Version",
    "Publication-Action",
    "Publication-Source-Issue",
    "Publication-Repository",
    "Publication-Allowed-Actions",
    "Publication-Forbidden-Actions",
    "Publication-Contract-Digest",
)
FIELD_RE_TEMPLATE = r"(?mi)^[ \t]*{field}:[ \t]*(.*?)[ \t]*$"
RETRY_ATTEMPT_RE = re.compile(r"(?mi)^[ \t]*Retry-Attempt:[ \t]*(\d+)[ \t]*$")
FAILURE_REASON_RE = re.compile(r"(?mi)^[ \t]*Failure-Reason:[ \t]*(.*?)[ \t]*\n?")
SELF_TEST_MARKER = "KOL_V4_RUNTIME_RETRY_SELF_TEST_PASS"


class RetryError(RuntimeError):
    pass


def field(text: str, name: str) -> str:
    pattern = re.compile(FIELD_RE_TEMPLATE.format(field=re.escape(name)))
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise RetryError(f"missing or duplicated field: {name}")
    value = matches[0].strip()
    if not value:
        raise RetryError(f"empty field: {name}")
    return value


def task_locations(runtime: Path, task_id: str) -> list[tuple[str, Path]]:
    queues = (
        "pending", "active", "review", "pending_codex", "approved",
        "completed", "blocked", "failed", "cancelled",
    )
    found: list[tuple[str, Path]] = []
    for queue in queues:
        path = runtime / "queue" / queue / f"{task_id}.md"
        if path.is_file() and not path.is_symlink():
            found.append((queue, path))
    return found


def git_text(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(project),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RetryError(f"git command failed: {' '.join(args[:2])}")
    return result.stdout.strip()


def validate_clean_kol_checkout(project: Path = KOL_PROJECT) -> None:
    resolved = project.resolve(strict=True)
    if resolved != project or not (project / ".git").is_dir():
        raise RetryError("KÖL checkout identity is invalid")
    if git_text(project, "branch", "--show-current") != KOL_BASE_BRANCH:
        raise RetryError("KÖL checkout must be on main")
    status = git_text(project, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RetryError("KÖL checkout must be clean")
    head = git_text(project, "rev-parse", "HEAD")
    remote = git_text(project, "rev-parse", "refs/remotes/origin/main")
    if head != remote:
        raise RetryError("KÖL local main must equal local origin/main")


def validate_failed_task(text: str, task_id: str) -> dict[str, str]:
    critical = {name: field(text, name) for name in CRITICAL_FIELDS}
    if critical["Task-ID"] != task_id:
        raise RetryError("task filename and Task-ID differ")
    if critical["Project-Path"] != str(KOL_PROJECT):
        raise RetryError("retry is restricted to the canonical KÖL checkout")
    if critical["Base-Branch"] != KOL_BASE_BRANCH:
        raise RetryError("KÖL retry requires main base branch")
    if critical["Publication-Contract-Version"] != str(kol_v4.VERSION):
        raise RetryError("retry requires KÖL V4 publication contract")
    if critical["Publication-Action"] != kol_v4.PUBLICATION_ACTION:
        raise RetryError("retry requires pull-request publication action")
    if critical["Publication-Repository"] != kol_v4.REPOSITORY:
        raise RetryError("retry publication repository mismatch")
    if critical["Required-Checks"] != EXPECTED_REQUIRED_CHECKS:
        raise RetryError("retry Required-Checks differ from proven live failure")
    source_issue = critical["Publication-Source-Issue"]
    if not source_issue.isdigit() or int(source_issue) <= 0:
        raise RetryError("invalid V4 source issue")
    if critical["Work-Branch"] != f"feature/chatgpt-issue-{source_issue}":
        raise RetryError("work branch does not match V4 source issue")

    attempt_matches = RETRY_ATTEMPT_RE.findall(text)
    if attempt_matches:
        raise RetryError("KÖL V4 runtime failure has already been retried")

    failure = field(text, "Failure-Reason")
    if "CODEX_STAGE01B_FAILED" not in failure or EXPECTED_FAILURE_FRAGMENT not in failure:
        raise RetryError("failed task is not the proven Stage 01B runtime-contract failure")
    return critical


def validate_runtime_paused(runtime: Path) -> None:
    pause = runtime / "run" / "paused"
    heartbeat = runtime / "run" / "heartbeat.json"
    if not pause.is_file() or pause.is_symlink():
        raise RetryError("Control Center pause guard is missing")
    try:
        state = json.loads(heartbeat.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RetryError("Control Center heartbeat is unreadable") from exc
    if not isinstance(state, dict) or state.get("state") != "paused":
        raise RetryError("Control Center heartbeat is not paused")


def acquire_supervisor_lock(runtime: Path):
    lock_path = runtime / "run" / "supervisor.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RetryError("Control Center supervisor is still running") from exc
    return handle


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def backup_evidence(runtime: Path, task: Path, task_id: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = runtime / "backups" / "kol-v4-runtime-retry"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / f"{task_id}-{stamp}"
    suffix = 0
    while destination.exists():
        suffix += 1
        destination = root / f"{task_id}-{stamp}-{suffix}"
    destination.mkdir(mode=0o700)
    shutil.copy2(task, destination / task.name)

    logs = runtime / "logs" / "orchestrator"
    for log in sorted(logs.glob(f"{task_id}-01B-*.log")):
        if log.is_file() and not log.is_symlink():
            shutil.copy2(log, destination / log.name)

    manifest_lines = []
    for path in sorted(destination.iterdir()):
        if not path.is_file() or path.is_symlink():
            raise RetryError("unsafe retry backup artifact")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_lines.append(f"{digest}  {path.name}")
    (destination / "SHA256SUMS").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )
    os.chmod(destination / "SHA256SUMS", 0o600)
    fsync_directory(destination)
    fsync_directory(root)
    return destination


def render_retry_task(original: str, backup: Path) -> str:
    critical_before = {name: field(original, name) for name in CRITICAL_FIELDS}
    updated, count = FAILURE_REASON_RE.subn("", original, count=1)
    if count != 1:
        raise RetryError("unable to remove exactly one Failure-Reason")
    updated = updated.rstrip() + "\n"
    original_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    updated += "Retry-Attempt: 1\n"
    updated += "Retry-Reason: stage01b-required-check-runtime-repaired\n"
    updated += f"Retry-Previous-Failure-SHA256: {original_sha}\n"
    updated += f"Retry-Evidence-Backup: {backup}\n"
    critical_after = {name: field(updated, name) for name in CRITICAL_FIELDS}
    if critical_after != critical_before:
        raise RetryError("retry preparation changed V4 authority metadata")
    if FAILURE_REASON_RE.search(updated):
        raise RetryError("stale Failure-Reason remains in retry task")
    return updated


def atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.retry-", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def retry_task(runtime: Path, task_id: str, project: Path = KOL_PROJECT) -> dict[str, str]:
    validate_runtime_paused(runtime)
    locations = task_locations(runtime, task_id)
    if len(locations) != 1 or locations[0][0] != "failed":
        raise RetryError("task must exist exactly once in failed")
    source = locations[0][1]
    original = source.read_text(encoding="utf-8", errors="strict")
    validate_failed_task(original, task_id)
    validate_clean_kol_checkout(project)
    stage01b_v2.verify_v2_required_check_contract()

    pending = runtime / "queue" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    target = pending / source.name
    if target.exists():
        raise RetryError("pending retry destination already exists")

    backup = backup_evidence(runtime, source, task_id)
    retry_text = render_retry_task(original, backup)
    atomic_write(source, retry_text)

    # Control Center is stopped and its lock is held by the caller. On the same
    # runtime filesystem, rename is the single atomic queue transition.
    os.rename(source, target)
    fsync_directory(source.parent)
    fsync_directory(target.parent)

    locations_after = task_locations(runtime, task_id)
    if locations_after != [("pending", target)]:
        raise RetryError("retry did not produce one authoritative pending task")
    return {
        "task_id": task_id,
        "queue": "pending",
        "backup": str(backup),
        "path": str(target),
    }


def run_self_test() -> int:
    # Pure contract checks only; the full filesystem transition is covered by
    # tests/test_kol_v4_runtime_retry.py.
    failure = (
        "Task-ID: KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938\n"
        f"Project-Path: {KOL_PROJECT}\n"
        "Base-Branch: main\n"
        "Work-Branch: feature/chatgpt-issue-172\n"
        "Goal: Harden deployment safety self-test diagnostics\n"
        f"Required-Checks: {EXPECTED_REQUIRED_CHECKS}\n"
        "Scope-Files: scripts/check-deployment-env-selftest.mjs\n"
        "Publication-Contract-Version: 4\n"
        "Publication-Action: pull-request\n"
        "Publication-Source-Issue: 172\n"
        "Publication-Repository: stvelikiy-star/kol-travel-platform\n"
        "Publication-Allowed-Actions: code-edit, commit, pull-request, push, tests\n"
        "Publication-Forbidden-Actions: database-mutation, deployment, destructive-operations, merge, other-project-access, payment-activation, production-change, scope-widening, secrets, supabase-restore\n"
        "Publication-Contract-Digest: " + "a" * 64 + "\n"
        "Failure-Reason: CODEX_STAGE01B_FAILED | CodexExecutionError: CODEX_STAGE01B_FAILED: "
        + EXPECTED_FAILURE_FRAGMENT
        + "\n"
    )
    validate_failed_task(failure, "KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938")
    print(SELF_TEST_MARKER)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument(
        "--state-root",
        default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)),
    )
    parser.add_argument("--task-id")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.task_id:
        parser.error("--task-id is required unless --self-test is used")

    root = Path(args.root).resolve()
    if root != DEFAULT_ROOT or not (root / "orchestrator").is_dir():
        raise RetryError("retry helper requires the canonical Control Center root")
    runtime = initialize(args.state_root)
    validate_runtime_paused(runtime)
    with acquire_supervisor_lock(runtime):
        result = retry_task(runtime, args.task_id)
    for key, value in result.items():
        print(f"{key}={value}")
    print("KOL_V4_RUNTIME_RETRY_PREPARED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RetryError as exc:
        print(f"KOL_V4_RUNTIME_RETRY_BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
