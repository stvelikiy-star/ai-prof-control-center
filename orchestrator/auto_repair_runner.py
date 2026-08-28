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
from pathlib import Path, PurePosixPath

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
AK_BERMET_PROJECT = Path("/home/agent/projects/ak-bermet")
LEGACY_AK_BERMET_INSPECTION_CHECK = (
    "node --test --experimental-strip-types src/lib/inspection-rules.test.ts"
)
AK_BERMET_INSPECTION_CHECK = "npm run test:inspection"


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


def latest_check_failure_log(paths: Paths, task_id: str) -> tuple[Path, re.Match[str]] | None:
    candidates = sorted(
        paths.logs.glob(f"{task_id}-01B-*.log"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for candidate in candidates:
        text = candidate.read_text(encoding="utf-8", errors="replace")
        if first_nonempty(candidate) not in {"CODEX_STAGE01B_FAILED", "CLAUDE_FAILED"}:
            continue
        match = CHECK_FAILURE_RE.search(text)
        if match:
            return candidate, match
    return None


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


def set_required_checks(task_text: str, checks: list[str]) -> str:
    replacement = "Required-Checks: " + ", ".join(checks)
    pattern = re.compile(FIELD_RE_TEMPLATE.format(field=re.escape("Required-Checks")))
    updated, count = pattern.subn(replacement, task_text, count=1)
    if count != 1:
        raise AutoRepairError("Required-Checks is missing")
    return updated


def validated_check_argv(command: str, required_checks: list[str]) -> list[str]:
    normalized = " ".join(command.split())
    normalized_required = {" ".join(item.split()) for item in required_checks}
    if normalized not in normalized_required:
        raise AutoRepairError(f"failed check is not in Required-Checks: {normalized}")
    argv = shlex.split(normalized)
    if not argv or argv[0] not in ALLOWED_CHECK_BINARIES:
        raise AutoRepairError(f"failed check binary is not allowlisted: {argv[:1]}")
    return argv


def normalize_legacy_ak_bermet_check(
    task_text: str, project: Path, command: str
) -> tuple[str, str]:
    if project != AK_BERMET_PROJECT or command != LEGACY_AK_BERMET_INSPECTION_CHECK:
        return task_text, command
    checks = parse_required_checks(task_text)
    if LEGACY_AK_BERMET_INSPECTION_CHECK not in checks:
        raise AutoRepairError("legacy AK BERMET check is absent from Required-Checks")
    checks = [
        AK_BERMET_INSPECTION_CHECK if item == LEGACY_AK_BERMET_INSPECTION_CHECK else item
        for item in checks
    ]
    return set_required_checks(task_text, checks), AK_BERMET_INSPECTION_CHECK


def parse_scope_files(task_text: str) -> tuple[str, ...]:
    raw = field(task_text, "Scope-Files")
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values or len(values) != len(set(values)):
        raise AutoRepairError("Scope-Files is empty or duplicated")
    for value in values:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or value != path.as_posix()
            or value in {".", ".."}
            or ".." in path.parts
            or any(part in {"", "."} for part in path.parts)
        ):
            raise AutoRepairError(f"unsafe Scope-Files entry: {value}")
    return values


def path_within_scope(
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


def git_run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(project),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        env=check_environment(),
        check=False,
    )
    if result.returncode != 0:
        raise AutoRepairError(f"git command failed: {' '.join(args[:2])}")
    return result


def _nul_paths(payload: str) -> set[str]:
    return {value for value in payload.split("\0") if value}


def dirty_paths(project: Path) -> set[str]:
    paths: set[str] = set()
    for args in (
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        paths.update(_nul_paths(git_run(project, *args).stdout))
    return paths


def backup_failed_candidate(project: Path, scope: tuple[str, ...], backup: Path) -> None:
    candidate = backup / "candidate"
    candidate.mkdir(mode=0o700)
    patch = git_run(project, "diff", "--binary", "HEAD", "--", *scope).stdout
    (candidate / "tracked.patch").write_text(patch, encoding="utf-8")
    for relative in sorted(dirty_paths(project)):
        source = project / relative
        if source.exists():
            if source.is_symlink() or not source.is_file():
                raise AutoRepairError(f"unsafe candidate path: {relative}")
            destination = candidate / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def restore_task_scope_to_head(project: Path, scope: tuple[str, ...]) -> None:
    dirty = dirty_paths(project)
    if not dirty:
        raise AutoRepairError("failed Stage 01B has no dirty implementation to restore")
    outside = sorted(
        relative
        for relative in dirty
        if not path_within_scope(project, relative, scope)
    )
    if outside:
        raise AutoRepairError(
            "refusing repair rollback with unrelated dirty paths: " + ",".join(outside)
        )

    tracked: list[str] = []
    untracked: list[str] = []
    for relative in sorted(dirty):
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=str(project),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            env=check_environment(),
            check=False,
        )
        (tracked if result.returncode == 0 else untracked).append(relative)

    if tracked:
        git_run(project, "restore", "--staged", "--worktree", "--source=HEAD", "--", *tracked)
    root = project.resolve()
    for relative in untracked:
        target = project / relative
        if target.is_symlink():
            raise AutoRepairError(f"unsafe untracked repair path: {relative}")
        parent = target.parent.resolve(strict=True)
        if parent != root and root not in parent.parents:
            raise AutoRepairError(f"untracked repair path escapes project: {relative}")
        if target.exists() and not target.is_file():
            raise AutoRepairError(f"unsafe untracked repair path: {relative}")
        target.unlink(missing_ok=True)

    remaining = dirty_paths(project)
    if remaining:
        raise AutoRepairError(
            "repair rollback did not restore clean worktree: " + ",".join(sorted(remaining))
        )


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
    failure = latest_check_failure_log(paths, task_id)
    if failure is None:
        return False
    log_01b, match = failure
    current_attempt = review_attempt(task_text)
    next_attempt = current_attempt + 1
    project_raw = field(task_text, "Project-Path")
    if not project_raw:
        raise AutoRepairError("Project-Path is missing")
    project = Path(project_raw).expanduser().resolve()
    if not project.is_dir():
        raise AutoRepairError(f"project is not accessible: {project}")
    command = match.group(1).strip()
    task_text, command = normalize_legacy_ak_bermet_check(task_text, project, command)
    required_checks = parse_required_checks(task_text)
    argv = validated_check_argv(command, required_checks)
    scope = parse_scope_files(task_text)

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

        feedback = "\n".join([
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

    backup = backup_evidence(paths, task, [log_01b])
    backup_failed_candidate(project, scope, backup)
    returncode, output = run_check(project, argv)
    restore_task_scope_to_head(project, scope)

    feedback = "\n".join([
        f"- Automatic-Repair-Cycle: {next_attempt}/{max_cycles}",
        f"- Failed-Required-Check: `{command}`",
        f"- Diagnostic-Recheck-Exit-Code: {returncode}",
        "- The failed implementation candidate was preserved in the auto-repair backup.",
        "- The task scope was restored to clean HEAD before this repair attempt.",
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
