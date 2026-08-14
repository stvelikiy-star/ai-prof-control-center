#!/usr/bin/env python3
"""Immutable AK BERMET DEV-only staff Auth activation operation.

Task prose is never executed. This helper pins the AK BERMET repository,
Supabase DEV identity, local credential-manifest location, fixed staff login
identifiers, and the provisioner argv. Secret values are loaded/generated only
on the Ubuntu host and are never included in result or error text.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


PROJECT = Path("/home/agent/projects/ak-bermet")
STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
STATE_WORKTREE_ROOT = STATE_ROOT / "runtime-worktrees"
SECURE_ROOT = STATE_ROOT / "secure"
MANIFEST = SECURE_ROOT / "ak-bermet-staff-auth.json"
EXPECTED_REMOTE_SUFFIX = "stvelikiy-star/ak-bermet.git"
EXPECTED_SUPABASE_HOST = "ednqgzgjhnalsiiuekmw.supabase.co"
EXPECTED_PROJECT_REF = "ednqgzgjhnalsiiuekmw"
ENV_FILES = (
    Path("/home/agent/.config/ai-prof-control-center/ak-bermet-runtime.env"),
    PROJECT / ".env.local",
    Path("/home/agent/.config/ai-prof-control-center/ak-bermet-release.env"),
)
REQUIRED_ENV = (
    "NEXT_PUBLIC_SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
)
OPTIONAL_ENV = ("SUPABASE_PROJECT_REF",)
ALLOWED_ENV = frozenset((*REQUIRED_ENV, *OPTIONAL_ENV))
UNSAFE_ENV = frozenset({
    "NODE_OPTIONS",
    "NODE_PATH",
    "NPM_CONFIG_SCRIPT_SHELL",
    "npm_config_script_shell",
    "AK_BERMET_AUTH_PROVISION_ENABLED",
    "AK_BERMET_AUTH_TARGET",
})

STAFF_SLOTS = (
    ("owner-1", "owner1@staff.akbermet.invalid"),
    ("administrator-1", "administrator1@staff.akbermet.invalid"),
    ("manager-1", "manager1@staff.akbermet.invalid"),
    ("manager-2", "manager2@staff.akbermet.invalid"),
    ("manager-3", "manager3@staff.akbermet.invalid"),
    ("manager-4", "manager4@staff.akbermet.invalid"),
    ("housekeeping-1", "housekeeping1@staff.akbermet.invalid"),
    ("housekeeping-2", "housekeeping2@staff.akbermet.invalid"),
    ("housekeeping-3", "housekeeping3@staff.akbermet.invalid"),
    ("housekeeping-4", "housekeeping4@staff.akbermet.invalid"),
    ("housekeeping-5", "housekeeping5@staff.akbermet.invalid"),
    ("housekeeping-6", "housekeeping6@staff.akbermet.invalid"),
    ("technician-1", "technician1@staff.akbermet.invalid"),
    ("technician-2", "technician2@staff.akbermet.invalid"),
    ("technician-3", "technician3@staff.akbermet.invalid"),
    ("technician-4", "technician4@staff.akbermet.invalid"),
    ("technician-5", "technician5@staff.akbermet.invalid"),
)

DRY_RESULT_RE = re.compile(r"^RESULT: PASS mode=dry-run slots=(\d+)$", re.MULTILINE)
EXECUTE_RESULT_RE = re.compile(
    r"^RESULT: PASS mode=execute created=(\d+) existing=(\d+) total=(\d+)$",
    re.MULTILINE,
)
BLOCKED_RE = re.compile(
    r"^BLOCKED: ([A-Z0-9_:.-]+)(?: slot=([a-z0-9-]+))?$",
    re.MULTILINE,
)


class StaffAuthActivationBlocked(RuntimeError):
    pass


class StaffAuthActivationFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class StaffAuthActivationResult:
    git_sha: str
    created: int
    existing: int
    total: int
    manifest_path: str

    def summary(self) -> str:
        return (
            f"staff_auth_dev_pass sha={self.git_sha} created={self.created} "
            f"existing={self.existing} total={self.total} manifest={self.manifest_path}"
        )


def _run(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
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
        raise StaffAuthActivationFailed("RUNTIME_COMMAND_TIMEOUT") from exc
    except OSError as exc:
        raise StaffAuthActivationBlocked("RUNTIME_COMMAND_UNAVAILABLE") from exc


def _git(repository: Path, env: dict[str, str], *args: str, timeout: int = 120) -> str:
    result = _run(["/usr/bin/git", *args], repository, env, timeout)
    if result.returncode != 0:
        raise StaffAuthActivationBlocked("GIT_RUNTIME_CHECK_FAILED")
    return result.stdout.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse only allowlisted KEY=VALUE lines and never evaluate shell syntax."""
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise StaffAuthActivationBlocked(f"ENV_FILE_UNREADABLE:{path.name}") from exc
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
    for name in UNSAFE_ENV:
        source.pop(name, None)

    collected: dict[str, str] = {
        key: source[key]
        for key in ALLOWED_ENV
        if source.get(key)
    }
    for path in ENV_FILES:
        for key, value in parse_env_file(path).items():
            collected.setdefault(key, value)

    missing = [key for key in REQUIRED_ENV if not collected.get(key)]
    if missing:
        raise StaffAuthActivationBlocked("MISSING_ENVIRONMENT:" + ",".join(missing))

    parsed = urlparse(collected["NEXT_PUBLIC_SUPABASE_URL"])
    if (
        parsed.scheme != "https"
        or parsed.hostname != EXPECTED_SUPABASE_HOST
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise StaffAuthActivationBlocked("SUPABASE_DEV_IDENTITY_MISMATCH")
    project_ref = collected.get("SUPABASE_PROJECT_REF")
    if project_ref and project_ref != EXPECTED_PROJECT_REF:
        raise StaffAuthActivationBlocked("SUPABASE_PROJECT_REF_MISMATCH")

    environment = source
    environment.update(collected)
    environment.pop("AK_BERMET_AUTH_PROVISION_ENABLED", None)
    environment.pop("AK_BERMET_AUTH_TARGET", None)
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
        raise StaffAuthActivationBlocked("AK_BERMET_REPOSITORY_UNAVAILABLE") from exc
    if resolved != PROJECT or not (resolved / ".git").is_dir():
        raise StaffAuthActivationBlocked("AK_BERMET_REPOSITORY_IDENTITY_MISMATCH")
    remote = _git(repository, env, "remote", "get-url", "origin", timeout=30)
    if not remote.removesuffix("/").endswith(EXPECTED_REMOTE_SUFFIX):
        raise StaffAuthActivationBlocked("AK_BERMET_REMOTE_IDENTITY_MISMATCH")


def _safe_node_modules(repository: Path) -> Path:
    node_modules = repository / "node_modules"
    try:
        resolved = node_modules.resolve(strict=True)
    except OSError as exc:
        raise StaffAuthActivationBlocked("NODE_MODULES_UNAVAILABLE") from exc
    if repository not in resolved.parents or not resolved.is_dir():
        raise StaffAuthActivationBlocked("NODE_MODULES_IDENTITY_MISMATCH")
    return resolved


def _secure_directory() -> Path:
    SECURE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = SECURE_ROOT.lstat()
    except OSError as exc:
        raise StaffAuthActivationBlocked("SECURE_ROOT_UNAVAILABLE") from exc
    if not stat.S_ISDIR(info.st_mode) or SECURE_ROOT.is_symlink():
        raise StaffAuthActivationBlocked("SECURE_ROOT_UNSAFE")
    if info.st_mode & 0o077:
        raise StaffAuthActivationBlocked("SECURE_ROOT_PERMISSIONS_UNSAFE")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise StaffAuthActivationBlocked("SECURE_ROOT_OWNER_MISMATCH")
    return SECURE_ROOT


def _new_password(slot: str, email: str, used: set[str]) -> str:
    local = email.split("@", 1)[0].lower()
    while True:
        password = secrets.token_urlsafe(24)
        lowered = password.lower()
        if (
            len(password) >= 20
            and password not in used
            and slot.lower() not in lowered
            and local not in lowered
        ):
            used.add(password)
            return password


def ensure_manifest() -> Path:
    _secure_directory()
    if MANIFEST.exists() or MANIFEST.is_symlink():
        return MANIFEST

    used: set[str] = set()
    manifest = {
        "version": 1,
        "project": "ak-bermet-dev",
        "slots": [
            {
                "slot": slot,
                "email": email,
                "password": _new_password(slot, email, used),
            }
            for slot, email in STAFF_SLOTS
        ],
    }
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(MANIFEST, flags, 0o600)
    except FileExistsError:
        return MANIFEST
    except OSError as exc:
        raise StaffAuthActivationBlocked("MANIFEST_CREATE_FAILED") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            MANIFEST.unlink()
        except OSError:
            pass
        raise
    try:
        info = MANIFEST.lstat()
    except OSError as exc:
        raise StaffAuthActivationBlocked("MANIFEST_POSTCREATE_UNAVAILABLE") from exc
    if not stat.S_ISREG(info.st_mode) or MANIFEST.is_symlink() or (info.st_mode & 0o077):
        raise StaffAuthActivationBlocked("MANIFEST_POSTCREATE_UNSAFE")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise StaffAuthActivationBlocked("MANIFEST_POSTCREATE_OWNER_MISMATCH")
    return MANIFEST


def _blocked_code(stdout: str, stderr: str) -> str | None:
    match = BLOCKED_RE.search(f"{stdout or ''}\n{stderr or ''}")
    if not match:
        return None
    code, slot = match.groups()
    return f"{code}:slot={slot}" if slot else code


def _parse_dry_result(result: subprocess.CompletedProcess[str]) -> None:
    match = DRY_RESULT_RE.search(result.stdout or "")
    if result.returncode == 0 and match and int(match.group(1)) == len(STAFF_SLOTS):
        return
    blocked = _blocked_code(result.stdout, result.stderr)
    if blocked:
        raise StaffAuthActivationBlocked("PROVISIONER_DRY_BLOCKED:" + blocked)
    raise StaffAuthActivationFailed(f"PROVISIONER_DRY_EXIT:{result.returncode}")


def _parse_execute_result(
    result: subprocess.CompletedProcess[str], sha: str,
) -> StaffAuthActivationResult:
    match = EXECUTE_RESULT_RE.search(result.stdout or "")
    if result.returncode == 0 and match:
        created, existing, total = map(int, match.groups())
        if total != len(STAFF_SLOTS) or created + existing != total:
            raise StaffAuthActivationFailed("PROVISIONER_EXECUTE_COUNT_MISMATCH")
        return StaffAuthActivationResult(
            sha, created, existing, total, str(MANIFEST)
        )
    blocked = _blocked_code(result.stdout, result.stderr)
    if blocked:
        raise StaffAuthActivationBlocked("PROVISIONER_EXECUTE_BLOCKED:" + blocked)
    raise StaffAuthActivationFailed(f"PROVISIONER_EXECUTE_EXIT:{result.returncode}")


def execute(
    node: Path, requested_path: str, base_environment: dict[str, str] | None = None,
) -> StaffAuthActivationResult:
    if requested_path != str(PROJECT):
        raise StaffAuthActivationBlocked("OPERATION_REPOSITORY_MISMATCH")
    environment = build_runtime_environment(base_environment)
    _validate_repository(PROJECT, environment)
    before = _owner_state(PROJECT, environment)
    node_modules = _safe_node_modules(PROJECT)

    fetch = _run(["/usr/bin/git", "fetch", "--quiet", "origin", "main"], PROJECT, environment, 180)
    if fetch.returncode != 0:
        raise StaffAuthActivationBlocked("GIT_FETCH_ORIGIN_MAIN_FAILED")
    sha = _git(PROJECT, environment, "rev-parse", "origin/main", timeout=30)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise StaffAuthActivationBlocked("ORIGIN_MAIN_SHA_INVALID")

    STATE_WORKTREE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STATE_WORKTREE_ROOT.is_symlink():
        raise StaffAuthActivationBlocked("RUNTIME_WORKTREE_ROOT_UNSAFE")
    temporary = Path(tempfile.mkdtemp(prefix="ak-bermet-staff-auth-", dir=STATE_WORKTREE_ROOT))
    temporary.rmdir()
    added = False
    cleanup_error = False
    activation: StaffAuthActivationResult | None = None
    try:
        add = _run(
            ["/usr/bin/git", "worktree", "add", "--detach", "--quiet", str(temporary), sha],
            PROJECT,
            environment,
            120,
        )
        if add.returncode != 0:
            raise StaffAuthActivationBlocked("TEMP_WORKTREE_CREATE_FAILED")
        added = True
        (temporary / "node_modules").symlink_to(node_modules, target_is_directory=True)
        provisioner = temporary / "scripts/staff-auth-provisioner.mjs"
        if not provisioner.is_file() or provisioner.is_symlink():
            raise StaffAuthActivationBlocked("STAFF_AUTH_PROVISIONER_UNAVAILABLE")

        manifest = ensure_manifest()
        dry_environment = dict(environment)
        dry_environment.pop("AK_BERMET_AUTH_PROVISION_ENABLED", None)
        dry_environment.pop("AK_BERMET_AUTH_TARGET", None)
        dry = _run(
            [str(node), str(provisioner), "--manifest", str(manifest)],
            temporary,
            dry_environment,
            120,
        )
        _parse_dry_result(dry)

        execute_environment = dict(environment)
        execute_environment["AK_BERMET_AUTH_PROVISION_ENABLED"] = "YES"
        execute_environment["AK_BERMET_AUTH_TARGET"] = "DEV"
        run = _run(
            [str(node), str(provisioner), "--manifest", str(manifest), "--execute"],
            temporary,
            execute_environment,
            300,
        )
        activation = _parse_execute_result(run, sha)
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
        raise StaffAuthActivationFailed("OWNER_WORKTREE_MUTATION_DETECTED")
    if cleanup_error:
        raise StaffAuthActivationFailed("TEMP_WORKTREE_CLEANUP_FAILED")
    if activation is None:
        raise StaffAuthActivationFailed("PROVISIONER_EXECUTE_NO_RESULT")
    return activation
