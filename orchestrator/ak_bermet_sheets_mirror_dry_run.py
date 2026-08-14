#!/usr/bin/env python3
"""Immutable, read-only AK BERMET Supabase -> Google Sheets mirror dry-run.

This module never executes task prose. It pins the DEV Supabase project, the
working workbook, the repository remote, and the worker argv. Runtime secrets
are loaded only from fixed local files/environment and their values are never
included in errors or result text.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


PROJECT = Path("/home/agent/projects/ak-bermet")
STATE_WORKTREE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center/runtime-worktrees")
EXPECTED_REMOTE_SUFFIX = "stvelikiy-star/ak-bermet.git"
EXPECTED_SUPABASE_HOST = "ednqgzgjhnalsiiuekmw.supabase.co"
EXPECTED_SPREADSHEET_ID = "16OAlbza9iNUKBHw87hq7OmryIF9PeXs28FJT3cXeSVk"
ENV_FILES = (
    Path("/home/agent/.config/ai-prof-control-center/ak-bermet-runtime.env"),
    PROJECT / ".env.local",
    Path("/home/agent/.config/ai-prof-control-center/ak-bermet-release.env"),
)
REQUIRED_ENV = (
    "NEXT_PUBLIC_SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "GOOGLE_SHEETS_SPREADSHEET_ID",
    "GOOGLE_SERVICE_ACCOUNT_EMAIL",
    "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY",
)
ALLOWED_ENV = frozenset(REQUIRED_ENV)
UNSAFE_NODE_ENV = frozenset({
    "NODE_OPTIONS",
    "NODE_PATH",
    "NPM_CONFIG_SCRIPT_SHELL",
    "npm_config_script_shell",
})
RESULT_RE = re.compile(r"^RESULT: PASS queue=(\d+)$", re.MULTILINE)
DRY_ACTION_RE = re.compile(r"^DRY: ([a-z0-9_]+) -> ([a-z_]+)$", re.MULTILINE)
DRY_BLOCKED_RE = re.compile(r"^DRY_BLOCKED: ([a-z0-9_]+) -> ([A-Z0-9_:.-]+)$", re.MULTILINE)


class RuntimeDryRunBlocked(RuntimeError):
    pass


class RuntimeDryRunFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeDryRunResult:
    git_sha: str
    queue_count: int
    planned_actions: tuple[str, ...]

    def summary(self) -> str:
        actions = ",".join(self.planned_actions) if self.planned_actions else "none"
        return f"dry_run_pass sha={self.git_sha} queue={self.queue_count} actions={actions}"


def _run(argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeDryRunFailed("RUNTIME_COMMAND_TIMEOUT") from exc
    except OSError as exc:
        raise RuntimeDryRunBlocked("RUNTIME_COMMAND_UNAVAILABLE") from exc


def _git(repository: Path, env: dict[str, str], *args: str, timeout: int = 120) -> str:
    result = _run(["/usr/bin/git", *args], repository, env, timeout)
    if result.returncode != 0:
        raise RuntimeDryRunBlocked("GIT_RUNTIME_CHECK_FAILED")
    return result.stdout.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse only allowlisted KEY=VALUE lines; never evaluate shell syntax."""
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeDryRunBlocked(f"ENV_FILE_UNREADABLE:{path.name}") from exc
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_ENV:
            continue
        value = _unquote(value.strip())
        if value and key not in values:
            values[key] = value
    return values


def build_runtime_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    source = dict(os.environ if base is None else base)
    for name in UNSAFE_NODE_ENV:
        source.pop(name, None)

    collected: dict[str, str] = {
        key: source[key]
        for key in REQUIRED_ENV
        if source.get(key)
    }
    for path in ENV_FILES:
        for key, value in parse_env_file(path).items():
            collected.setdefault(key, value)

    missing = [key for key in REQUIRED_ENV if not collected.get(key)]
    if missing:
        raise RuntimeDryRunBlocked("MISSING_ENVIRONMENT:" + ",".join(missing))

    parsed = urlparse(collected["NEXT_PUBLIC_SUPABASE_URL"])
    if parsed.scheme != "https" or parsed.hostname != EXPECTED_SUPABASE_HOST:
        raise RuntimeDryRunBlocked("SUPABASE_DEV_IDENTITY_MISMATCH")
    if collected["GOOGLE_SHEETS_SPREADSHEET_ID"] != EXPECTED_SPREADSHEET_ID:
        raise RuntimeDryRunBlocked("GOOGLE_SHEETS_IDENTITY_MISMATCH")

    environment = source
    environment.update(collected)
    environment["GOOGLE_SHEETS_ENABLED"] = "true"
    # Defense in depth: execution can never be enabled by a stale host env.
    environment.pop("AK_BERMET_SHEETS_MIRROR_ENABLED", None)
    return environment


def _owner_state(repository: Path, env: dict[str, str]) -> tuple[str, str, str]:
    return (
        _git(repository, env, "branch", "--show-current", timeout=30),
        _git(repository, env, "rev-parse", "HEAD", timeout=30),
        _git(repository, env, "status", "--porcelain=v1", "--untracked-files=all", timeout=30),
    )


def _validate_repository(repository: Path, env: dict[str, str]) -> None:
    try:
        resolved = repository.resolve(strict=True)
    except OSError as exc:
        raise RuntimeDryRunBlocked("AK_BERMET_REPOSITORY_UNAVAILABLE") from exc
    if resolved != PROJECT or not (resolved / ".git").is_dir():
        raise RuntimeDryRunBlocked("AK_BERMET_REPOSITORY_IDENTITY_MISMATCH")
    remote = _git(repository, env, "remote", "get-url", "origin", timeout=30)
    normalized = remote.removesuffix("/")
    if not normalized.endswith(EXPECTED_REMOTE_SUFFIX):
        raise RuntimeDryRunBlocked("AK_BERMET_REMOTE_IDENTITY_MISMATCH")


def _safe_node_modules(repository: Path) -> Path:
    node_modules = repository / "node_modules"
    try:
        resolved = node_modules.resolve(strict=True)
    except OSError as exc:
        raise RuntimeDryRunBlocked("NODE_MODULES_UNAVAILABLE") from exc
    if repository not in resolved.parents or not resolved.is_dir():
        raise RuntimeDryRunBlocked("NODE_MODULES_IDENTITY_MISMATCH")
    return resolved


def _parse_worker_result(stdout: str, stderr: str, returncode: int, sha: str) -> RuntimeDryRunResult:
    match = RESULT_RE.search(stdout or "")
    if returncode == 0 and match:
        actions = tuple(f"{table}:{action}" for table, action in DRY_ACTION_RE.findall(stdout or ""))
        return RuntimeDryRunResult(sha, int(match.group(1)), actions)
    blocked = DRY_BLOCKED_RE.findall(f"{stdout or ''}\n{stderr or ''}")
    if blocked:
        codes = ",".join(f"{table}:{code}" for table, code in blocked)
        raise RuntimeDryRunBlocked("WORKER_BLOCKED:" + codes)
    raise RuntimeDryRunFailed(f"WORKER_DRY_RUN_EXIT:{returncode}")


def execute(node: Path, requested_path: str, base_environment: dict[str, str] | None = None) -> RuntimeDryRunResult:
    if requested_path != str(PROJECT):
        raise RuntimeDryRunBlocked("OPERATION_REPOSITORY_MISMATCH")
    environment = build_runtime_environment(base_environment)
    _validate_repository(PROJECT, environment)
    before = _owner_state(PROJECT, environment)
    node_modules = _safe_node_modules(PROJECT)

    fetch = _run(["/usr/bin/git", "fetch", "--quiet", "origin", "main"], PROJECT, environment, 180)
    if fetch.returncode != 0:
        raise RuntimeDryRunBlocked("GIT_FETCH_ORIGIN_MAIN_FAILED")
    sha = _git(PROJECT, environment, "rev-parse", "origin/main", timeout=30)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeDryRunBlocked("ORIGIN_MAIN_SHA_INVALID")

    STATE_WORKTREE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STATE_WORKTREE_ROOT.is_symlink():
        raise RuntimeDryRunBlocked("RUNTIME_WORKTREE_ROOT_UNSAFE")
    temporary = Path(tempfile.mkdtemp(prefix="ak-bermet-sheets-", dir=STATE_WORKTREE_ROOT))
    added = False
    cleanup_error = False
    result: RuntimeDryRunResult | None = None
    try:
        add = _run(
            ["/usr/bin/git", "worktree", "add", "--detach", "--quiet", str(temporary), sha],
            PROJECT,
            environment,
            120,
        )
        if add.returncode != 0:
            raise RuntimeDryRunBlocked("TEMP_WORKTREE_CREATE_FAILED")
        added = True
        (temporary / "node_modules").symlink_to(node_modules, target_is_directory=True)
        worker = temporary / "scripts/sheets-sync-worker.mjs"
        if not worker.is_file() or worker.is_symlink():
            raise RuntimeDryRunBlocked("SHEETS_MIRROR_WORKER_UNAVAILABLE")
        run = _run(
            [str(node), str(worker), "--dry-run", "--limit=25"],
            temporary,
            environment,
            180,
        )
        result = _parse_worker_result(run.stdout, run.stderr, run.returncode, sha)
    finally:
        if added:
            remove = _run(
                ["/usr/bin/git", "worktree", "remove", "--force", str(temporary)],
                PROJECT,
                environment,
                120,
            )
            cleanup_error = remove.returncode != 0
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        _run(["/usr/bin/git", "worktree", "prune"], PROJECT, environment, 60)

    after = _owner_state(PROJECT, environment)
    if before != after:
        raise RuntimeDryRunFailed("OWNER_WORKTREE_MUTATION_DETECTED")
    if cleanup_error:
        raise RuntimeDryRunFailed("TEMP_WORKTREE_CLEANUP_FAILED")
    if result is None:
        raise RuntimeDryRunFailed("WORKER_DRY_RUN_NO_RESULT")
    return result
