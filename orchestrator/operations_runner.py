#!/usr/bin/env python3
"""Strict allowlisted runner for production and runtime operations.

Task prose is metadata only. Every executable and argument is constructed
locally from an immutable profile, and every child uses shell=False.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ak_bermet_release_v6_prepare as release_v6
import ak_bermet_sheets_mirror_dry_run as sheets_mirror
import ak_bermet_staff_auth_activate_dev as staff_auth
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
PYTHON3_CLI = Path("/usr/bin/python3")
HEALTH_TEST_MODULES = (
    "tests.test_ai_prof_approved_task_publisher_gate",
    "tests.test_control_loop",
    "tests.test_self_maintenance_profile",
)
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
NVM_NODE_VERSIONS = Path("/home/agent/.nvm/versions/node")
UNSAFE_ENVIRONMENT_NAMES = frozenset({
    "NODE_OPTIONS",
    "NODE_PATH",
    "NPM_CONFIG_SCRIPT_SHELL",
    "npm_config_script_shell",
})
UNSAFE_HEALTH_ENVIRONMENT_NAMES = UNSAFE_ENVIRONMENT_NAMES | {
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "PYTHONBREAKPOINT",
    "PYTHONWARNINGS",
}
LEGACY_SHEETS_DRY_RUN_GOAL = "Sheets mirror runtime dry-run"
LEGACY_SHEETS_DRY_RUN_PROFILE = "ak-bermet-sheets-mirror-dry-run"
LEGACY_SHEETS_DRY_RUN_PROJECT = "/home/agent/projects/ak-bermet"
LEGACY_STAFF_AUTH_ACTIVATE_GOAL = "Activate 17 DEV staff accounts"
LEGACY_STAFF_AUTH_ACTIVATE_PROFILE = "ak-bermet-staff-auth-activate-dev"
LEGACY_STAFF_AUTH_ACTIVATE_PROJECT = "/home/agent/projects/ak-bermet"


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


def operation_environment(node_bin: Path) -> dict[str, str]:
    """Copy the service environment and make the selected NVM Node available."""
    environment = os.environ.copy()
    for name in UNSAFE_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    current_path = environment.get("PATH", "")
    environment["PATH"] = (
        f"{node_bin}{os.pathsep}{current_path}" if current_path else str(node_bin)
    )
    return environment


def read_only_health_environment() -> dict[str, str]:
    """Return a sanitized environment for fixed read-only Python checks."""
    environment = os.environ.copy()
    for name in UNSAFE_HEALTH_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def run_argv(
    argv: list[str], repository: Path, environment: dict[str, str], *,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
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
                env=environment,
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


def git_output(repository: Path, environment: dict[str, str], *args: str) -> str:
    result = run_argv(
        [str(GIT_CLI), *args], repository, environment, timeout=60,
    )
    return result.stdout.strip()


def validate_repository(
    profile: OperationProfile, requested_path: str, environment: dict[str, str],
) -> Path:
    if requested_path != str(profile.repository):
        raise OperationBlocked("operation repository does not exactly match registered path")
    try:
        repository = profile.repository.resolve(strict=True)
    except OSError as exc:
        raise OperationBlocked(f"registered repository is unavailable: {exc}") from exc
    if repository != profile.repository or not (repository / ".git").is_dir():
        raise OperationBlocked("registered repository path is not an exact Git worktree")
    if git_output(repository, environment, "branch", "--show-current") != profile.base_branch:
        raise OperationBlocked(f"repository must be on {profile.base_branch}")
    if git_output(repository, environment, "status", "--porcelain"):
        raise OperationBlocked("repository working tree is dirty")
    if profile.kind == "migration":
        expected = repository / profile.expected_migration
        if not expected.is_file() or expected.is_symlink():
            raise OperationBlocked("registered migration is missing or unsafe")
    return repository


def locate_node_bin() -> Path:
    candidates: list[tuple[tuple[int, ...], Path]] = []
    try:
        entries = list(NVM_NODE_VERSIONS.iterdir())
    except OSError:
        raise OperationBlocked("NODE_RUNTIME_NOT_FOUND") from None
    for entry in entries:
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", entry.name)
        node = entry / "bin/node"
        if match and node.is_file() and os.access(node, os.X_OK):
            candidates.append((tuple(map(int, match.groups())), entry / "bin"))
    if not candidates:
        raise OperationBlocked("NODE_RUNTIME_NOT_FOUND")
    return max(candidates, key=lambda candidate: candidate[0])[1]


def locate_toolchain(repository: Path, node_bin: Path) -> Toolchain:
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


def execute_release_v6_prepare(profile: OperationProfile, requested_path: str) -> str:
    if requested_path != str(profile.repository):
        raise OperationBlocked("operation repository does not exactly match registered path")
    if profile.kind != "release-v6-prepare":
        raise OperationBlocked("invalid release prepare operation kind")
    report = release_v6.prepare()
    if report.repository != str(profile.repository):
        raise OperationFailed("release prepare report repository mismatch")
    if report.production_changed:
        raise OperationFailed("read-only release prepare reported a production mutation")
    blockers = list(report.blockers or [])
    if blockers:
        safe = ",".join(redact(item).replace("\n", " ")[:180] for item in blockers)
        raise OperationBlocked(f"AK_BERMET_V6_PREPARE:{safe}")
    return "release_ready"


def execute_sheets_mirror_dry_run(profile: OperationProfile, requested_path: str) -> str:
    if requested_path != str(profile.repository):
        raise OperationBlocked("operation repository does not exactly match registered path")
    if profile.kind != "sheets-mirror-dry-run":
        raise OperationBlocked("invalid Sheets mirror operation kind")
    node_bin = locate_node_bin()
    environment = operation_environment(node_bin)
    try:
        result = sheets_mirror.execute(node_bin / "node", requested_path, environment)
    except sheets_mirror.RuntimeDryRunBlocked as exc:
        raise OperationBlocked(str(exc)) from exc
    except sheets_mirror.RuntimeDryRunFailed as exc:
        raise OperationFailed(str(exc)) from exc
    return result.summary()


def execute_staff_auth_activate_dev(profile: OperationProfile, requested_path: str) -> str:
    if requested_path != str(profile.repository):
        raise OperationBlocked("operation repository does not exactly match registered path")
    if profile.kind != "staff-auth-activate-dev":
        raise OperationBlocked("invalid staff Auth activation operation kind")
    node_bin = locate_node_bin()
    environment = operation_environment(node_bin)
    try:
        result = staff_auth.execute(node_bin / "node", requested_path, environment)
    except staff_auth.StaffAuthActivationBlocked as exc:
        raise OperationBlocked(str(exc)) from exc
    except staff_auth.StaffAuthActivationFailed as exc:
        raise OperationFailed(str(exc)) from exc
    return result.summary()


def execute_control_center_health_check(
    profile: OperationProfile, requested_path: str,
) -> str:
    """Run fixed read-only reconciliation checks in the real maintenance tree."""
    if profile.kind != "control-center-health-check":
        raise OperationBlocked("invalid control-center health operation kind")
    environment = read_only_health_environment()
    repository = validate_repository(profile, requested_path, environment)
    head = git_output(repository, environment, "rev-parse", "HEAD")
    origin_main = git_output(
        repository, environment, "rev-parse", "--verify", "origin/main"
    )
    try:
        run_argv(
            [
                str(GIT_CLI), "merge-base", "--is-ancestor",
                "origin/main", "HEAD",
            ],
            repository,
            environment,
            timeout=60,
        )
    except OperationFailed as exc:
        raise OperationBlocked(
            "HEALTH_ORIGIN_MAIN_NOT_ANCESTOR_OF_MAINTENANCE_HEAD"
        ) from exc

    run_argv(
        [str(PYTHON3_CLI), "-m", "unittest", *HEALTH_TEST_MODULES],
        repository,
        environment,
    )
    run_argv(
        [str(PYTHON3_CLI), "-m", "unittest"],
        repository,
        environment,
    )

    if git_output(repository, environment, "rev-parse", "HEAD") != head:
        raise OperationFailed("health checks changed repository HEAD")
    if git_output(repository, environment, "status", "--porcelain"):
        raise OperationFailed("health checks changed the working tree")
    return json.dumps(
        {
            "branch": profile.base_branch,
            "focused_tests": "PASS",
            "full_tests": "PASS",
            "head": head,
            "origin_main": origin_main,
            "profile": profile.key,
            "status": "PASS",
            "working_tree": "clean",
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def execute_profile(profile: OperationProfile, requested_path: str) -> str:
    if profile.kind == "control-center-health-check":
        return execute_control_center_health_check(profile, requested_path)
    if profile.kind == "release-v6-prepare":
        return execute_release_v6_prepare(profile, requested_path)
    if profile.kind == "sheets-mirror-dry-run":
        return execute_sheets_mirror_dry_run(profile, requested_path)
    if profile.kind == "staff-auth-activate-dev":
        return execute_staff_auth_activate_dev(profile, requested_path)
    if profile.kind != "migration":
        raise OperationBlocked("unsupported operation profile kind")

    node_bin = locate_node_bin()
    environment = operation_environment(node_bin)
    repository = validate_repository(profile, requested_path, environment)
    tools = locate_toolchain(repository, node_bin)
    expected = migration_id(profile)

    listing = run_argv(
        supabase_command(tools, "migration", "list", "--linked"),
        repository, environment, retry_transient=True,
    )
    _local, remote = linked_migrations(listing.stdout)
    already_applied = expected in remote
    if not already_applied:
        dry_run = run_argv(
            supabase_command(tools, "db", "push", "--linked", "--dry-run"),
            repository, environment, retry_transient=True,
        )
        pending = pending_from_dry_run(f"{dry_run.stdout}\n{dry_run.stderr}")
        if pending != {expected}:
            raise OperationBlocked(
                "pending migrations do not exactly match the registered migration"
            )
        run_argv(
            supabase_command(tools, "db", "push", "--linked"),
            repository, environment, retry_transient=True,
        )

    verified = run_argv(
        supabase_command(tools, "migration", "list", "--linked"),
        repository, environment, retry_transient=True,
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
            run_argv(argv, repository, environment)
    finally:
        restore_tsbuildinfo(repository, before)

    if git_output(repository, environment, "status", "--porcelain"):
        raise OperationFailed("operation finished with a dirty working tree")
    return "already_applied" if already_applied else "applied"


def legacy_runtime_profile(data: dict[str, str]) -> str | None:
    """Narrow compatibility bridge for already-published Telegram commands.

    Generic /ai task intake predates runtime operation profiles and labels the
    task as code. Only exact immutable goal/project pairs are promoted. The
    user-supplied Instructions and Scope-Files are never executed by this path.
    """
    if data.get("Execution-Mode") != "code" or data.get("Operation-Profile") != "none":
        return None
    project = data.get("Project-Path")
    goal = data.get("Goal")
    if project == LEGACY_SHEETS_DRY_RUN_PROJECT and goal == LEGACY_SHEETS_DRY_RUN_GOAL:
        return LEGACY_SHEETS_DRY_RUN_PROFILE
    if project == LEGACY_STAFF_AUTH_ACTIVATE_PROJECT and goal == LEGACY_STAFF_AUTH_ACTIVATE_GOAL:
        return LEGACY_STAFF_AUTH_ACTIVATE_PROFILE
    return None


def _terminal_reason(path: Path, field: str, reason: str) -> None:
    safe = re.sub(r"[\x00-\x1f\x7f]+", " ", redact(reason)).strip()[:500] or "UNKNOWN"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    pattern = re.compile(rf"(?mi)^\s*{re.escape(field)}:\s*[^\r\n]*$")
    line = f"{field}: {safe}"
    if pattern.search(text):
        updated = pattern.sub(line, text, count=1)
    else:
        updated = text.rstrip("\n") + "\n" + line + "\n"
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return


def process_one(paths: orch.Paths) -> int:
    task: Path | None = None
    promoted_profile: str | None = None
    for candidate in sorted(paths.pending.glob("*.md")):
        try:
            data, _ = orch.parse_task(candidate)
        except Exception:
            continue
        profile = legacy_runtime_profile(data)
        if data["Execution-Mode"] == "operations" or profile:
            task = candidate
            promoted_profile = profile
            break
    if task is None:
        print("QUEUE_EMPTY")
        return 0
    active = orch.safe_move(task, paths.active)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = paths.logs / f"{active.stem}-operations-{timestamp}.log"
    try:
        data, _task_text = orch.parse_task(active)
        profile_key = promoted_profile or data["Operation-Profile"]
        try:
            profile = resolve_profile(profile_key)
        except ValueError as exc:
            raise OperationBlocked(str(exc)) from exc
        outcome = execute_profile(profile, data["Project-Path"])
        summary = (
            "PASS\n"
            f"task_id={data['Task-ID']}\n"
            f"profile={profile.key}\n"
            f"outcome={outcome}\n"
            f"legacy_runtime_promotion={'true' if promoted_profile else 'false'}\n"
            "task_text_executed=false\nshell=false\nworking_tree=preserved\n"
        )
        log_path.write_text(redact(summary), encoding="utf-8")
        orch.safe_move(active, paths.completed)
        print("PASS")
        return 0
    except OperationBlocked as exc:
        safe = redact(str(exc))
        _terminal_reason(active, "Blocked-Reason", safe)
        log_path.write_text(redact(f"BLOCKED\n{safe}\n"), encoding="utf-8")
        if active.exists():
            orch.safe_move(active, paths.blocked)
        print("BLOCKED", file=sys.stderr)
        return 1
    except OperationFailed as exc:
        safe = redact(str(exc))
        _terminal_reason(active, "Failure-Reason", safe)
        log_path.write_text(redact(f"FAILED\n{safe}\n"), encoding="utf-8")
        if active.exists():
            orch.safe_move(active, paths.failed)
        print("FAILED", file=sys.stderr)
        return 1
    except Exception as exc:
        reason = f"UNEXPECTED_{type(exc).__name__}"
        _terminal_reason(active, "Failure-Reason", reason)
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
