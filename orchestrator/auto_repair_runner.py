#!/usr/bin/env python3
"""Bounded automatic recovery for repairable AI PROF code-task failures.

The runner handles only two proven, code-repairable states:
1. Stage 01B applied scoped changes but a required check failed.
2. Stage 01C was blocked only because the configured review-cycle cap was
   increased after a successful Stage 01B repair.

Security/access/infrastructure blockers remain blocked and require no
uncontrolled retry. Every automatic queue transition is backed up first.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
DEFAULT_STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
DEFAULT_MAX_FIX_CYCLES = 5
CHECK_TIMEOUT_SECONDS = 900
MAX_DIAGNOSTIC_BYTES = 12000
SELF_TEST_MARKER = "AUTO_REPAIR_RUNNER_SELF_TEST_PASS"

ATTEMPT_RE = re.compile(r"(?mi)^[ \t]*Codex-Review-Attempt:[ \t]*(\d+)[ \t]*$")
FIELD_RE_TEMPLATE = r"(?mi)^[ \t]*{field}:[ \t]*(.*?)[ \t]*$"
CHECK_FAILURE_RE = re.compile(r"check failed:[ \t]*(.+?)(?:\n|$)", re.IGNORECASE)
AUTO_FEEDBACK_MARKER = "## Automated Stage 01B Repair Feedback"
AUTO_FEEDBACK_RE = re.compile(r"\n" + re.escape(AUTO_FEEDBACK_MARKER) + r"\n.*\Z", re.DOTALL)
SECRET_PATTERNS = (
    re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
)
ALLOWED_CHECK_BINARIES = {"git", "node", "npm", "npx", "python3"}


class AutoRepairError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path
    runtime: Path
    failed: Path
    blocked: Path
    review: Path
    pending_codex: Path
    logs: Path
    backups: Path
    lock: Path


def build_paths(root: Path, state_root: Path) -> Paths:
    runtime = state_root.resolve()
    paths = Paths(
        root=root.resolve(),
        runtime=runtime,
        failed=runtime / "queue" / "failed",
        blocked=runtime / "queue" / "blocked",
        review=runtime / "queue" / "review",
        pending_codex=runtime / "queue" / "pending_codex",
        logs=runtime / "logs" / "orchestrator",
        backups=runtime / "backups" / "auto-repair",
        lock=runtime / "run" / "auto-repair.lock",
    )
    for directory in (
        paths.failed,
        paths.blocked,
        paths.review,
        paths.pending_codex,
        paths.logs,
        paths.backups,
        paths.lock.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def acquire_lock(path: Path):
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise
    return handle


def redact(text: str) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    encoded = result.encode("utf-8", "replace")
    if len(encoded) > MAX_DIAGNOSTIC_BYTES:
        encoded = encoded[-MAX_DIAGNOSTIC_BYTES:]
        result = "[TRUNCATED]\n" + encoded.decode("utf-8", "replace")
    return result


def field(text: str, name: str) -> str:
    pattern = re.compile(FIELD_RE_TEMPLATE.format(field=re.escape(name)))
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def review_attempt(text: str) -> int:
    matches = ATTEMPT_RE.findall(text)
    if not matches:
        return 0
    if len(matches) != 1:
        raise AutoRepairError("malformed or duplicated Codex-Review-Attempt")
    return int(matches[0])


def set_review_attempt(text: str, value: int) -> str:
    line = f"Codex-Review-Attempt: {value}"
    if ATTEMPT_RE.search(text):
        updated, count = ATTEMPT_RE.subn(line, text, count=1)
        if count != 1:
            raise AutoRepairError("unable to update review attempt")
        return updated
    return text.rstrip() + "\n" + line + "\n"


def set_auto_feedback(text: str, feedback: str) -> str:
    clean = AUTO_FEEDBACK_RE.sub("", text.rstrip())
    return clean + "\n\n" + AUTO_FEEDBACK_MARKER + "\n" + feedback.rstrip() + "\n"


def load_max_fix_cycles(root: Path) -> int:
    try:
        data = json.loads((root / "orchestrator" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_MAX_FIX_CYCLES
    value = data.get("max_fix_cycles")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_MAX_FIX_CYCLES


def latest_log(paths: Paths, task_id: str, stage: str) -> Path | None:
    candidates = list(paths.logs.glob(f"{task_id}-{stage}-*.log"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def first_nonempty(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            return line.strip()
    return ""


def atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def move_no_replace(source: Path, destination_dir: Path) -> Path:
    destination = destination_dir / source.name
    if destination.exists():
        raise AutoRepairError(f"destination already exists: {destination}")
    os.rename(source, destination)
    return destination


def backup_evidence(paths: Paths, task: Path, logs: list[Path]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = paths.backups / f"{task.stem}-{stamp}"
    suffix = 0
    while destination.exists():
        suffix += 1
        destination = paths.backups / f"{task.stem}-{stamp}-{suffix}"
    destination.mkdir(parents=True, mode=0o700)
    shutil.copy2(task, destination / task.name)
    for log in logs:
        if log and log.is_file():
            shutil.copy2(log, destination / log.name)
    return destination


def parse_required_checks(task_text: str) -> list[str]:
    raw = field(task_text, "Required-Checks")
    checks = [item.strip() for item in raw.split(",") if item.strip()]
    if not checks:
        raise AutoRepairError("Required-Checks is empty")
    return checks


def validated_check_argv(command: str, required_checks: list[str]) -> list[str]:
    normalized = " ".join(command.split())
    normalized_required = {" ".join(item.split()) for item in required_checks}
    if normalized not in normalized_required:
        raise AutoRepairError(f"failed check is not in Required-Checks: {normalized}")
    argv = shlex.split(normalized)
    if not argv or argv[0] not in ALLOWED_CHECK_BINARIES:
        raise AutoRepairError(f"failed check binary is not allowlisted: {argv[:1]}")
    return argv


def check_environment() -> dict[str, str]:
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    required = ("node", "npm", "npx")
    if all(shutil.which(name, path=current_path) for name in required):
        return env
    versions = Path.home() / ".nvm" / "versions" / "node"
    candidates = sorted(versions.glob("*/bin"), reverse=True)
    for candidate in candidates:
        candidate_path = f"{candidate}:{current_path}"
        if all(shutil.which(name, path=candidate_path) for name in required):
            env["PATH"] = candidate_path
            return env
    return env


def run_check(project: Path, argv: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        argv,
        cwd=str(project),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=CHECK_TIMEOUT_SECONDS,
        env=check_environment(),
        check=False,
    )
    return result.returncode, redact(result.stdout or "")


def recover_review_limit(paths: Paths, task: Path, max_cycles: int) -> bool:
    task_text = task.read_text(encoding="utf-8")
    attempt = review_attempt(task_text)
    if attempt >= max_cycles:
        return False
    task_id = field(task_text, "Task-ID") or task.stem
    log_01c = latest_log(paths, task_id, "01C")
    log_01b = latest_log(paths, task_id, "01B")
    if not log_01c or not log_01b:
        return False
    if first_nonempty(log_01c) != "BLOCKED_REVIEW_ATTEMPTS_EXCEEDED":
        return False
    text_01b = log_01b.read_text(encoding="utf-8", errors="replace")
    if "STAGE_01B_CODEX_PASS" not in text_01b and "STAGE_01B_CLAUDE_PASS" not in text_01b:
        return False
    backup = backup_evidence(paths, task, [log_01b, log_01c])
    destination = move_no_replace(task, paths.pending_codex)
    print("AUTO_REPAIR_AUDIT_LIMIT_REQUEUED")
    print(f"task_id={task_id}")
    print(f"review_attempt={attempt}")
    print(f"max_fix_cycles={max_cycles}")
    print(f"backup={backup}")
    print(f"queue={destination.parent.name}")
    return True


def recover_check_failure(paths: Paths, task: Path, max_cycles: int) -> bool:
    task_text = task.read_text(encoding="utf-8")
    task_id = field(task_text, "Task-ID") or task.stem
    log_01b = latest_log(paths, task_id, "01B")
    if not log_01b:
        return False
    log_text = log_01b.read_text(encoding="utf-8", errors="replace")
    if first_nonempty(log_01b) not in {"CODEX_STAGE01B_FAILED", "CLAUDE_FAILED"}:
        return False
    match = CHECK_FAILURE_RE.search(log_text)
    if not match:
        return False
    current_attempt = review_attempt(task_text)
    next_attempt = current_attempt + 1
    if next_attempt > max_cycles:
        return False
    project_raw = field(task_text, "Project-Path")
    if not project_raw:
        raise AutoRepairError("Project-Path is missing")
    project = Path(project_raw).expanduser().resolve()
    if not project.is_dir():
        raise AutoRepairError(f"project is not accessible: {project}")
    command = match.group(1).strip()
    required_checks = parse_required_checks(task_text)
    argv = validated_check_argv(command, required_checks)
    returncode, output = run_check(project, argv)
    feedback = "\n".join([
        f"- Automatic-Repair-Cycle: {next_attempt}/{max_cycles}",
        f"- Failed-Required-Check: `{command}`",
        f"- Diagnostic-Recheck-Exit-Code: {returncode}",
        "- Fix the confirmed implementation or type/test/build defect directly.",
        "- Do not weaken, delete, skip, mock, or bypass any required check.",
        "- Preserve all prior Codex audit feedback and security requirements.",
        "- Exact diagnostic output follows:",
        "```text",
        output.rstrip() or "(no output)",
        "```",
    ])
    updated = set_review_attempt(task_text, next_attempt)
    updated = set_auto_feedback(updated, feedback)
    backup = backup_evidence(paths, task, [log_01b])
    atomic_write(task, updated)
    destination = move_no_replace(task, paths.review)
    print("AUTO_REPAIR_CHECK_FAILURE_REQUEUED")
    print(f"task_id={task_id}")
    print(f"repair_cycle={next_attempt}/{max_cycles}")
    print(f"failed_check={command}")
    print(f"diagnostic_exit_code={returncode}")
    print(f"backup={backup}")
    print(f"queue={destination.parent.name}")
    return True


def process_one(paths: Paths) -> int:
    max_cycles = load_max_fix_cycles(paths.root)
    for task in sorted(paths.blocked.glob("*.md")):
        if recover_review_limit(paths, task, max_cycles):
            return 0
    for task in sorted(paths.failed.glob("*.md")):
        if recover_check_failure(paths, task, max_cycles):
            return 0
    print("AUTO_REPAIR_QUEUE_EMPTY")
    return 0


def run_self_test() -> int:
    sample = "Task-ID: TEST\nRequired-Checks: npx tsc --noEmit\nCodex-Review-Attempt: 2\n"
    if review_attempt(sample) != 2:
        raise RuntimeError("SELF_TEST_ATTEMPT_PARSE_FAILED")
    updated = set_review_attempt(sample, 3)
    if review_attempt(updated) != 3:
        raise RuntimeError("SELF_TEST_ATTEMPT_UPDATE_FAILED")
    feedback = set_auto_feedback(updated, "- Exact diagnostic output follows:\n```text\nerror\n```")
    if feedback.count(AUTO_FEEDBACK_MARKER) != 1:
        raise RuntimeError("SELF_TEST_FEEDBACK_FAILED")
    argv = validated_check_argv("npx tsc --noEmit", ["npx tsc --noEmit"])
    if argv != ["npx", "tsc", "--noEmit"]:
        raise RuntimeError("SELF_TEST_CHECK_ALLOWLIST_FAILED")
    if "[REDACTED]" not in redact("token=secret-value"):
        raise RuntimeError("SELF_TEST_REDACTION_FAILED")
    print(SELF_TEST_MARKER)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--state-root", default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = build_paths(root, Path(args.state_root))
    try:
        lock = acquire_lock(paths.lock)
    except BlockingIOError:
        print("AUTO_REPAIR_ALREADY_RUNNING", file=sys.stderr)
        return 2
    with lock:
        if args.self_test:
            return run_self_test()
        return process_one(paths)


if __name__ == "__main__":
    raise SystemExit(main())
