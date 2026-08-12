#!/usr/bin/env python3
"""Read-only AK BERMET Production Activation V6 readiness probe.

This module intentionally cannot migrate or deploy production. It verifies the
frozen release SHA, repository cleanliness, application/release environment
names, structural preflight, exact linked migration ledger, backup evidence,
and public-site fingerprint. Unresolved production authority remains an
explicit blocker.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

PROJECT = Path("/home/agent/projects/ak-bermet")
SECRET_FILE = Path("/home/agent/.config/ai-prof-control-center/ak-bermet-release.env")
APP_ENV_FILE = PROJECT / ".env.local"
BACKUP_ROOT = Path("/home/agent/ai-prof-backups/ak-bermet")
FROZEN_SHA = "e26ced6a187a654baf858bc8b13044f4123f0a8a"
PUBLIC_URL = "https://akbermet.kg/"
NVM_NODE_VERSIONS = Path("/home/agent/.nvm/versions/node")

REQUIRED_RELEASE_ENV = (
    "SUPABASE_ACCESS_TOKEN",
    "SUPABASE_PROJECT_REF",
    "SUPABASE_DB_PASSWORD",
    "NEXT_PUBLIC_SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
)
REQUIRED_ENV = REQUIRED_RELEASE_ENV  # backward-compatible test/API alias

REQUIRED_APP_ENV = (
    "GOOGLE_SHEETS_ENABLED",
    "GOOGLE_SHEETS_SPREADSHEET_ID",
    "GOOGLE_SERVICE_ACCOUNT_EMAIL",
    "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY",
)

EXPECTED_MIGRATIONS = (
    "20260721000100",
    "20260721000200",
    "20260721000300",
    "20260721000400",
    "20260721000500",
    "20260721000600",
    "20260721000700",
    "20260721000800",
    "20260721000900",
    "20260722001100",
    "20260722001200",
    "20260722001300",
    "20260722001400",
    "20260722001500",
    "20260722001600",
    "20260722001700",
    "20260727000100",
    "20260728000100",
)
MIGRATION_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")
UNSAFE_ENV = {"NODE_OPTIONS", "NODE_PATH", "NPM_CONFIG_SCRIPT_SHELL", "npm_config_script_shell"}
LEGACY_MARKERS = ("Hotel Prime", "Make Booking", "Proceed to checkout", "Сделать заказ")


class PrepareBlocked(RuntimeError):
    pass


@dataclass
class PrepareReport:
    frozen_sha: str
    repository: str
    git_head: str = "UNKNOWN"
    git_branch: str = "UNKNOWN"
    worktree_clean: bool = False
    environment_names: str = "PENDING"
    structural_preflight: str = "PENDING"
    production_preflight: str = "PENDING"
    migration_ledger: str = "PENDING"
    migration_action: str = "SKIPPED"
    backup_evidence: str = "PENDING"
    restore_smoke: str = "PENDING"
    public_site: str = "PENDING"
    deployment_target: str = "UNVERIFIED"
    production_changed: bool = False
    blockers: list[str] | None = None

    def __post_init__(self) -> None:
        if self.blockers is None:
            self.blockers = []


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stdout or result.stderr or "command failed").strip()
        raise PrepareBlocked(f"READ_ONLY_COMMAND_FAILED:{Path(argv[0]).name}:{detail[:300]}")
    return result


def clean_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in UNSAFE_ENV:
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def git_output(*args: str) -> str:
    return run(["/usr/bin/git", *args], cwd=PROJECT, env=clean_environment(), timeout=60).stdout.strip()


def validate_repository(report: PrepareReport) -> None:
    if PROJECT.is_symlink() or not PROJECT.is_dir() or not (PROJECT / ".git").is_dir():
        raise PrepareBlocked("PROJECT_GIT_REPOSITORY_UNAVAILABLE")
    report.git_head = git_output("rev-parse", "HEAD")
    report.git_branch = git_output("branch", "--show-current")
    report.worktree_clean = not bool(git_output("status", "--porcelain"))
    if report.git_head != FROZEN_SHA:
        raise PrepareBlocked("FROZEN_SHA_MISMATCH")
    if report.git_branch != "main":
        raise PrepareBlocked("RELEASE_BRANCH_MISMATCH")
    if not report.worktree_clean:
        raise PrepareBlocked("PROJECT_WORKTREE_NOT_CLEAN")


def _read_allowlisted_environment(path: Path, allowed: tuple[str, ...], *, require_private_mode: bool) -> dict[str, str]:
    try:
        info = path.stat()
    except OSError as exc:
        raise PrepareBlocked("ENVIRONMENT_FILE_UNAVAILABLE") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise PrepareBlocked("ENVIRONMENT_FILE_INVALID")
    if require_private_mode and stat.S_IMODE(info.st_mode) & 0o077:
        raise PrepareBlocked("ENVIRONMENT_FILE_MODE_INVALID")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="strict").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PrepareBlocked("ENVIRONMENT_FILE_FORMAT_INVALID")
        key, value = line.split("=", 1)
        key = key.strip()
        if key in allowed:
            values[key] = value.strip()
    return values


def read_secret_environment() -> dict[str, str]:
    try:
        values = _read_allowlisted_environment(
            SECRET_FILE,
            REQUIRED_RELEASE_ENV,
            require_private_mode=True,
        )
    except PrepareBlocked as exc:
        code = str(exc)
        mapping = {
            "ENVIRONMENT_FILE_UNAVAILABLE": "RELEASE_SECRET_FILE_UNAVAILABLE",
            "ENVIRONMENT_FILE_INVALID": "RELEASE_SECRET_FILE_FORMAT_INVALID",
            "ENVIRONMENT_FILE_MODE_INVALID": "RELEASE_SECRET_FILE_MODE_INVALID",
            "ENVIRONMENT_FILE_FORMAT_INVALID": "RELEASE_SECRET_FILE_FORMAT_INVALID",
        }
        raise PrepareBlocked(mapping.get(code, code)) from exc
    if any(not values.get(key) for key in REQUIRED_RELEASE_ENV):
        raise PrepareBlocked("RELEASE_ENVIRONMENT_INCOMPLETE")
    return values


def read_app_environment() -> dict[str, str]:
    try:
        values = _read_allowlisted_environment(
            APP_ENV_FILE,
            REQUIRED_APP_ENV,
            require_private_mode=True,
        )
    except PrepareBlocked as exc:
        code = str(exc)
        mapping = {
            "ENVIRONMENT_FILE_UNAVAILABLE": "APP_ENVIRONMENT_FILE_UNAVAILABLE",
            "ENVIRONMENT_FILE_INVALID": "APP_ENVIRONMENT_FILE_INVALID",
            "ENVIRONMENT_FILE_MODE_INVALID": "APP_ENVIRONMENT_FILE_MODE_INVALID",
            "ENVIRONMENT_FILE_FORMAT_INVALID": "APP_ENVIRONMENT_FILE_FORMAT_INVALID",
        }
        raise PrepareBlocked(mapping.get(code, code)) from exc
    if any(not values.get(key) for key in REQUIRED_APP_ENV):
        raise PrepareBlocked("APP_ENVIRONMENT_INCOMPLETE")
    if values["GOOGLE_SHEETS_ENABLED"] != "true":
        raise PrepareBlocked("GOOGLE_SHEETS_NOT_ENABLED_FOR_PRODUCTION")
    return values


def locate_node_bin() -> Path:
    candidates: list[tuple[tuple[int, int, int], Path]] = []
    try:
        entries = list(NVM_NODE_VERSIONS.iterdir())
    except OSError as exc:
        raise PrepareBlocked("NODE_RUNTIME_NOT_FOUND") from exc
    for entry in entries:
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", entry.name)
        node = entry / "bin/node"
        if match and node.is_file() and os.access(node, os.X_OK):
            version = tuple(map(int, match.groups()))
            if version[0] >= 22:
                candidates.append((version, entry / "bin"))
    if not candidates:
        raise PrepareBlocked("NODE_22_RUNTIME_NOT_FOUND")
    return max(candidates, key=lambda item: item[0])[1]


def validate_preflight(
    report: PrepareReport,
    release_env: dict[str, str],
    app_env: dict[str, str],
) -> tuple[Path, dict[str, str]]:
    """Run only structural preflight, then validate the V6 read-only env contract.

    `npm run preflight:production` intentionally includes operator-only backup
    gates such as AK_BERMET_BACKUP_APPROVED=YES. Those belong to the future
    explicit backup action and must not be required or synthesized during a
    read-only preparation probe.
    """
    node_bin = locate_node_bin()
    node = node_bin / "node"
    env = clean_environment({**release_env, **app_env})
    env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
    run([str(node), "scripts/production-preflight.mjs"], cwd=PROJECT, env=env)
    report.structural_preflight = "PASS"
    report.production_preflight = "PASS_V6_ENV_CONTRACT"
    return node, env


def local_migrations() -> set[str]:
    root = PROJECT / "supabase/migrations"
    found: set[str] = set()
    for path in root.glob("*.sql"):
        match = MIGRATION_RE.match(path.name)
        if match:
            found.add(match.group(1))
    return found


def linked_migrations(output: str) -> tuple[set[str], set[str]]:
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


def validate_migration_ledger(report: PrepareReport, node: Path, env: dict[str, str]) -> None:
    expected = set(EXPECTED_MIGRATIONS)
    if local_migrations() != expected:
        raise PrepareBlocked("LOCAL_MIGRATION_SET_DIVERGED")
    supabase = PROJECT / "node_modules/.bin/supabase"
    if not supabase.is_file():
        raise PrepareBlocked("LOCAL_SUPABASE_CLI_UNAVAILABLE")
    result = run([str(node), str(supabase), "migration", "list", "--linked"], cwd=PROJECT, env=env)
    linked_local, linked_remote = linked_migrations(result.stdout)
    if linked_local != expected or linked_remote != expected:
        raise PrepareBlocked("LINKED_MIGRATION_LEDGER_DIVERGED")
    report.migration_ledger = "PASS_18_OF_18"
    report.migration_action = "SKIPPED_ALREADY_RECONCILED"


def newest_backup() -> Path:
    try:
        candidates = [path for path in BACKUP_ROOT.iterdir() if path.is_dir() and not path.is_symlink()]
    except OSError as exc:
        raise PrepareBlocked("BACKUP_ROOT_UNAVAILABLE") from exc
    if not candidates:
        raise PrepareBlocked("BACKUP_EVIDENCE_MISSING")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def validate_backup_evidence(report: PrepareReport) -> None:
    root = newest_backup()
    digests: list[str] = []
    for name in ("roles.sql", "schema.sql", "data.sql"):
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise PrepareBlocked(f"BACKUP_ARTIFACT_INVALID:{name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digests.append(digest)
    if len(set(digests)) != 3:
        raise PrepareBlocked("BACKUP_ARTIFACT_HASH_COLLISION")
    report.backup_evidence = f"PASS:{root.name}"
    report.restore_smoke = "REQUIRED_BEFORE_PRODUCTION_CHANGE"


def inspect_public_site(report: PrepareReport) -> None:
    request = urllib.request.Request(PUBLIC_URL, headers={"User-Agent": "AI-PROF-release-readiness/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(500_000).decode("utf-8", "replace")
            final_url = response.geturl()
    except Exception as exc:
        raise PrepareBlocked("PUBLIC_SITE_DISCOVERY_FAILED") from exc
    if any(marker.casefold() in body.casefold() for marker in LEGACY_MARKERS):
        report.public_site = f"LEGACY_CONFIRMED:{final_url}"
    else:
        report.public_site = f"UNKNOWN_RUNTIME:{final_url}"
    report.deployment_target = "UNVERIFIED"


def prepare() -> PrepareReport:
    report = PrepareReport(frozen_sha=FROZEN_SHA, repository=str(PROJECT))
    try:
        validate_repository(report)
        release_env = read_secret_environment()
        app_env = read_app_environment()
        report.environment_names = "PASS"
        node, env = validate_preflight(report, release_env, app_env)
        validate_migration_ledger(report, node, env)
        validate_backup_evidence(report)
        inspect_public_site(report)
    except PrepareBlocked as exc:
        report.blockers.append(str(exc))
        return report
    report.blockers.extend([
        "RESTORE_SMOKE_REQUIRED_BEFORE_PRODUCTION_CHANGE",
        "DEPLOYMENT_TARGET_UNVERIFIED",
        "ROLLBACK_SAFE_CUTOVER_UNVERIFIED",
    ])
    return report


def render(report: PrepareReport) -> str:
    payload = asdict(report)
    return json.dumps(payload, sort_keys=True, indent=2)


def main() -> int:
    report = prepare()
    print(render(report))
    return 0 if not report.blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
