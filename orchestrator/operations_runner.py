#!/usr/bin/env python3
"""Strict allowlisted runner for production operations.

Task prose is metadata only. Every executable and argument is constructed
locally from an immutable profile, and every child uses shell=False.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from operation_profiles import OperationProfile, resolve_profile
from runtime_paths import DEFAULT_STATE_ROOT


_ORCHESTRATOR_PATH = Path(__file__).resolve().parent / "orchestrator.py"
_SPEC = importlib.util.spec_from_file_location("ai_prof_operations_orchestrator", _ORCHESTRATOR_PATH)
orch = importlib.util.module_from_spec(_SPEC)
if _SPEC.loader is None:
    raise RuntimeError("Cannot load orchestrator core")
sys.modules[_SPEC.name] = orch
_SPEC.loader.exec_module(orch)

COMMAND_TIMEOUT_SECONDS = 900
TRANSIENT_ATTEMPTS = 3
GIT_CLI = Path("/usr/bin/git")
MIGRATION_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")
TRANSIENT_POSTGRES_MARKERS = (
    "connection refused", "connection reset", "connection timed out",
    "timeout expired", "server closed the connection", "could not connect",
    "temporary failure", "network is unreachable", "broken pipe",
    "connection terminated unexpectedly", "too many connections",
)
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(authorization)\s*:\s*(?:bearer\s+)?[^\s,;]+"),
    re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|https?)://[^\s]+"),
    re.compile(r"\b(?:sk|sbp)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]+)?\b"),
)


class OperationBlocked(RuntimeError):
    pass


class OperationFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class Toolchain:
    node: Path
    npm: Path
    npx: Path
    supabase: Path


def redact(text: str) -> str:
    safe = text
    for pattern in SECRET_PATTERNS:
        safe = pattern.sub("[REDACTED]", safe)
    return orch.redact(safe)


def run_argv(
    argv: list[str], repository: Path, *, timeout: int = COMMAND_TIMEOUT_SECONDS,
    retry_transient: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one preconstructed argv. This is the only child-process boundary."""
    attempts = TRANSIENT_ATTEMPTS if retry_transient else 1
    last: subprocess.CompletedProcess[str] | None = None
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                argv,
                cwd=str(repository),
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            if retry_transient and attempt + 1 < attempts:
                continue
            raise OperationFailed(f"command timed out after {timeout}s") from exc
        except OSError as exc:
            raise OperationBlocked(f"unable to launch registered command: {exc}") from exc
        last = result
        combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        if result.returncode == 0:
            return result
        if not retry_transient or not any(marker in combined for marker in TRANSIENT_POSTGRES_MARKERS):
            break
    assert last is not None
    detail = redact(f"{last.stdout or ''}\n{last.stderr or ''}").strip()[:1000]
    raise OperationFailed(f"registered command failed ({last.returncode}): {detail}")


def git_output(repository: Path, *args: str) -> str:
    result = run_argv([str(GIT_CLI), *args], repository, timeout=60)
    return result.stdout.strip()


def validate_repository(profile: OperationProfile, requested_path: str) -> Path:
    if requested_path != str(profile.repository):
        raise OperationBlocked("operation repository does not exactly match registered path")
    try:
        repository = profile.repository.resolve(strict=True)
    except OSError as exc:
        raise OperationBlocked(f"registered repository is unavailable: {exc}") from exc
    if repository != profile.repository or not (repository / ".git").is_dir():
        raise OperationBlocked("registered repository path is not an exact Git worktree")
    if git_output(repository, "branch", "--show-current") != profile.base_branch:
        raise OperationBlocked(f"repository must be on {profile.base_branch}")
    if git_output(repository, "status", "--porcelain"):
        raise OperationBlocked("repository working tree is dirty")
    expected = repository / profile.expected_migration
    if not expected.is_file() or expected.is_symlink():
        raise OperationBlocked("registered migration is missing or unsafe")
    return repository


def locate_toolchain(repository: Path) -> Toolchain:
    versions = Path("/home/agent/.nvm/versions/node")
    candidates: list[tuple[tuple[int, ...], Path]] = []
    try:
        entries = list(versions.iterdir())
    except OSError as exc:
        raise OperationBlocked(f"Node versions directory unavailable: {exc}") from exc
    for entry in entries:
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", entry.name)
        node = entry / "bin/node"
        if match and node.is_file() and os.access(node, os.X_OK):
            candidates.append((tuple(map(int, match.groups())), entry / "bin"))
    if not candidates:
        raise OperationBlocked("no executable Node installation found")
    node_bin = max(candidates)[1]
    supabase = repository / "node_modules/.bin/supabase"
    tools = Toolchain(node_bin / "node", node_bin / "npm", node_bin / "npx", supabase)
    for tool in tools.__dict__.values():
        try:
            mode = tool.stat().st_mode
        except OSError as exc:
            raise OperationBlocked(f"registered tool unavailable: {tool.name}: {exc}") from exc
        if not stat.S_ISREG(mode) or not os.access(tool, os.X_OK):
            raise OperationBlocked(f"registered tool is not executable: {tool.name}")
    node_modules = (repository / "node_modules").resolve()
    if node_modules not in supabase.resolve().parents:
        raise OperationBlocked("Supabase CLI escapes repository-local node_modules")
    return tools


def migration_id(profile: OperationProfile) -> str:
    match = MIGRATION_RE.search(Path(profile.expected_migration).name)
    if not match:
        raise OperationBlocked("registered migration has no valid version")
    return match.group(1)


def linked_migrations(output: str) -> tuple[set[str], set[str]]:
    """Return local and remote migration IDs from `migration list` rows."""
    local: set[str] = set()
    remote: set[str] = set()
    for line in output.splitlines():
        if "|" not in line:
            continue
        columns = line.split("|")
        if len(columns) < 2:
            continue
        left = MIGRATION_RE.search(columns[0])
        right = MIGRATION_RE.search(columns[1])
        if left:
            local.add(left.group(1))
        if right:
            remote.add(right.group(1))
    return local, remote


def pending_from_dry_run(output: str) -> set[str]:
    return set(MIGRATION_RE.findall(output))


def supabase_command(tools: Toolchain, *args: str) -> list[str]:
    return [str(tools.node), str(tools.supabase), *args]


def restore_tsbuildinfo(repository: Path, before: bytes | None) -> None:
    path = repository / "tsconfig.tsbuildinfo"
    if before is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        path.write_bytes(before)


def execute_profile(profile: OperationProfile, requested_path: str) -> str:
    repository = validate_repository(profile, requested_path)
    tools = locate_toolchain(repository)
    expected = migration_id(profile)

    listing = run_argv(
        supabase_command(tools, "migration", "list", "--linked"),
        repository, retry_transient=True,
    )
    _local, remote = linked_migrations(listing.stdout)
    already_applied = expected in remote
    if not already_applied:
        dry_run = run_argv(
            supabase_command(tools, "db", "push", "--linked", "--dry-run"),
            repository, retry_transient=True,
        )
        pending = pending_from_dry_run(f"{dry_run.stdout}\n{dry_run.stderr}")
        if pending != {expected}:
            raise OperationBlocked(
                "pending migrations do not exactly match the registered migration"
            )
        run_argv(
            supabase_command(tools, "db", "push", "--linked"),
            repository, retry_transient=True,
        )

    verified = run_argv(
        supabase_command(tools, "migration", "list", "--linked"),
        repository, retry_transient=True,
    )
    _local, verified_remote = linked_migrations(verified.stdout)
    if expected not in verified_remote:
        raise OperationFailed("registered migration is not present in linked migration history")

    tsbuildinfo = repository / "tsconfig.tsbuildinfo"
    before = tsbuildinfo.read_bytes() if tsbuildinfo.is_file() else None
    try:
        commands = (
            [str(tools.npm), "run", "lint"],
            [str(tools.npx), "tsc", "--noEmit"],
            [
                str(tools.node), "--test", "--experimental-strip-types",
                "src/lib/inspection-rules.test.ts",
            ],
            [str(tools.npm), "run", "build"],
        )
        for argv in commands:
            run_argv(argv, repository)
    finally:
        restore_tsbuildinfo(repository, before)

    if git_output(repository, "status", "--porcelain"):
        raise OperationFailed("operation finished with a dirty working tree")
    return "already_applied" if already_applied else "applied"


def process_one(paths: orch.Paths) -> int:
    task: Path | None = None
    for candidate in sorted(paths.pending.glob("*.md")):
        try:
            data, _ = orch.parse_task(candidate)
        except Exception:
            continue
        if data["Execution-Mode"] == "operations":
            task = candidate
            break
    if task is None:
        print("QUEUE_EMPTY")
        return 0
    active = orch.safe_move(task, paths.active)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = paths.logs / f"{active.stem}-operations-{timestamp}.log"
    try:
        data, _task_text = orch.parse_task(active)
        try:
            profile = resolve_profile(data["Operation-Profile"])
        except ValueError as exc:
            raise OperationBlocked(str(exc)) from exc
        outcome = execute_profile(profile, data["Project-Path"])
        summary = (
            "PASS\n"
            f"task_id={data['Task-ID']}\n"
            f"profile={profile.key}\n"
            f"migration={outcome}\n"
            "task_text_executed=false\nshell=false\nworking_tree=clean\n"
        )
        log_path.write_text(redact(summary), encoding="utf-8")
        orch.safe_move(active, paths.completed)
        print("PASS")
        return 0
    except OperationBlocked as exc:
        log_path.write_text(redact(f"BLOCKED\n{exc}\n"), encoding="utf-8")
        if active.exists():
            orch.safe_move(active, paths.blocked)
        print("BLOCKED", file=sys.stderr)
        return 1
    except Exception as exc:
        log_path.write_text(redact(f"FAILED\n{type(exc).__name__}: {exc}\n"), encoding="utf-8")
        if active.exists():
            orch.safe_move(active, paths.failed)
        print("FAILED", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/agent/projects/ai-prof-control-center")
    parser.add_argument("--state-root", default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = orch.build_paths(root, args.state_root)
    try:
        lock = orch.acquire_lock(paths.lock)
    except BlockingIOError:
        print("ORCHESTRATOR_ALREADY_RUNNING", file=sys.stderr)
        return 2
    with lock:
        return process_one(paths)


if __name__ == "__main__":
    raise SystemExit(main())
