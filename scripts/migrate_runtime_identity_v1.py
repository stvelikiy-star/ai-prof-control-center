#!/usr/bin/env python3
"""Migrate AI PROF to one agent-owned systemd runtime.

This repairs the Mobile Control V1 bootstrap where system services were started
as root while historical user services remained active. It keeps the old user
control surface available while code/tests/units are prepared, then performs a
short cutover. On cutover failure the historical user services are restarted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

LIVE = Path("/home/agent/projects/ai-prof-control-center")
BACKUP_ROOT = Path("/home/agent/ai-prof-backups/control-center")
SYSTEM_UNITS = (
    "ai-prof-control-center.service",
    "ai-prof-telegram-bridge.service",
    "ai-prof-github-task-gateway.service",
)
USER_UNITS = (
    "ai-prof-control-center.service",
    "ai-prof-telegram-bridge.service",
)
EXPECTED_REPOSITORY = "stvelikiy-star/ai-prof-control-center"


class MigrationError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path | None = None, check: bool = True, capture: bool = False):
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise MigrationError(f"command failed ({argv[0]}): {detail[:1600]}")
    return result


def git(*args: str, capture: bool = True) -> str:
    result = run(["git", *args], cwd=LIVE, capture=capture)
    return (result.stdout or "").strip()


def sudo(*args: str, check: bool = True, capture: bool = False):
    return run(["sudo", "-n", *args], check=check, capture=capture)


def userctl(*args: str, check: bool = True, capture: bool = False):
    return run(["systemctl", "--user", *args], check=check, capture=capture)


def system_active(name: str) -> bool:
    return sudo("systemctl", "is-active", "--quiet", name, check=False).returncode == 0


def user_active(name: str) -> bool:
    return userctl("is-active", "--quiet", name, check=False).returncode == 0


def verify(approved_sha: str) -> dict[str, object]:
    if os.geteuid() == 0:
        raise MigrationError("run as normal agent user, not root")
    if not LIVE.is_dir() or LIVE.is_symlink() or not (LIVE / ".git").exists():
        raise MigrationError("live Control Center checkout unavailable")
    if len(approved_sha) != 40:
        raise MigrationError("approved SHA must be exact 40-char SHA")
    if git("status", "--porcelain"):
        raise MigrationError("live Control Center worktree is dirty")
    if shutil.which("gh") is None:
        raise MigrationError("GitHub CLI is required")
    sudo("true")
    auth = run(
        ["gh", "api", f"repos/{EXPECTED_REPOSITORY}", "--jq", 'if .private == true then "PRIVATE" else "NOT_PRIVATE" end'],
        capture=True,
    )
    if (auth.stdout or "").strip() != "PRIVATE":
        raise MigrationError("private Control Center repository is not accessible as agent")
    git("fetch", "--prune", "origin", "main", capture=False)
    remote = git("rev-parse", "origin/main")
    if remote != approved_sha:
        raise MigrationError(f"origin/main moved: expected {approved_sha}, got {remote}")
    head = git("rev-parse", "HEAD")
    if run(["git", "merge-base", "--is-ancestor", head, approved_sha], cwd=LIVE, check=False).returncode != 0:
        raise MigrationError("current live HEAD is not ancestor of approved main")
    return {
        "head": head,
        "branch": git("branch", "--show-current"),
        "user_active": {name: user_active(name) for name in USER_UNITS},
    }


def checkpoint(meta: dict[str, object], approved_sha: str) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = BACKUP_ROOT / f"runtime-identity-{stamp}"
    root.mkdir(parents=True, mode=0o700)
    (root / "PREVIOUS_SHA").write_text(str(meta["head"]) + "\n", encoding="utf-8")
    (root / "APPROVED_SHA").write_text(approved_sha + "\n", encoding="utf-8")
    (root / "USER_ACTIVE.txt").write_text(repr(meta["user_active"]) + "\n", encoding="utf-8")
    for name in SYSTEM_UNITS:
        source = Path("/etc/systemd/system") / name
        if source.exists():
            sudo("cp", "--preserve=mode,timestamps", str(source), str(root / name))
            sudo("chown", f"{os.getuid()}:{os.getgid()}", str(root / name))
    return root


def stop_bad_system_runtime() -> None:
    for name in reversed(SYSTEM_UNITS):
        sudo("systemctl", "stop", name, check=False)


def update_checkout_and_units(approved_sha: str) -> None:
    current_branch = git("branch", "--show-current")
    if current_branch != "main":
        raise MigrationError("live checkout must already be on main before runtime migration")
    git("merge", "--ff-only", approved_sha, capture=False)
    if git("rev-parse", "HEAD") != approved_sha:
        raise MigrationError("checkout did not reach approved SHA")
    run(["python3", "test_control_center.py"], cwd=LIVE)
    run(["python3", "-m", "unittest", "-v"], cwd=LIVE)
    for name in SYSTEM_UNITS:
        source = LIVE / "systemd" / name
        if not source.is_file():
            raise MigrationError(f"missing staged unit: {name}")
        sudo("install", "-m", "0644", str(source), f"/etc/systemd/system/{name}")
    sudo("systemctl", "daemon-reload")


def stop_user_runtime() -> None:
    for name in reversed(USER_UNITS):
        userctl("stop", name, check=False)


def restore_user_runtime(meta: dict[str, object]) -> None:
    active = meta.get("user_active", {})
    if isinstance(active, dict):
        for name in USER_UNITS:
            if active.get(name):
                userctl("restart", name, check=False)


def start_system_runtime() -> None:
    for name in SYSTEM_UNITS:
        sudo("systemctl", "enable", name)
        sudo("systemctl", "restart", name)
    for name in SYSTEM_UNITS:
        if not system_active(name):
            logs = sudo("journalctl", "-u", name, "-n", "50", "--no-pager", check=False, capture=True)
            detail = (logs.stdout or logs.stderr or "inactive").strip()
            raise MigrationError(f"{name} inactive after restart: {detail[-2500:]}")


def verify_single_runtime() -> None:
    for name in SYSTEM_UNITS:
        show = sudo("systemctl", "show", name, "-p", "User", "-p", "MainPID", capture=True)
        text = show.stdout or ""
        if "User=agent" not in text:
            raise MigrationError(f"{name} is not agent-owned")
    ps = run(["ps", "-eo", "user=,pid=,args="], capture=True).stdout or ""
    telegram = [line for line in ps.splitlines() if "orchestrator/telegram_bridge" in line and "grep" not in line]
    legacy = [line for line in telegram if "telegram_bridge.py" in line and "telegram_bridge_v2.py" not in line]
    v2 = [line for line in telegram if "telegram_bridge_v2.py" in line]
    gateways = [line for line in ps.splitlines() if "orchestrator/github_task_gateway.py" in line]
    controls = [line for line in ps.splitlines() if "orchestrator/control_loop_service.py" in line]
    if legacy:
        raise MigrationError("legacy Telegram process is still running")
    if len(v2) != 1 or not v2[0].lstrip().startswith("agent "):
        raise MigrationError(f"expected one agent-owned Telegram V2 process, found {len(v2)}")
    if len(gateways) != 1 or not gateways[0].lstrip().startswith("agent "):
        raise MigrationError(f"expected one agent-owned GitHub gateway process, found {len(gateways)}")
    if len(controls) != 1 or not controls[0].lstrip().startswith("agent "):
        raise MigrationError(f"expected one agent-owned Control Center service process, found {len(controls)}")
    for name in USER_UNITS:
        if user_active(name):
            raise MigrationError(f"historical user service still active: {name}")


def disable_old_user_units() -> None:
    for name in USER_UNITS:
        userctl("disable", name, check=False)


def migrate(approved_sha: str) -> Path:
    meta = verify(approved_sha)
    saved = checkpoint(meta, approved_sha)
    stop_bad_system_runtime()
    update_checkout_and_units(approved_sha)
    stop_user_runtime()
    try:
        start_system_runtime()
        verify_single_runtime()
    except Exception:
        stop_bad_system_runtime()
        restore_user_runtime(meta)
        raise
    disable_old_user_units()
    return saved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-sha", required=True)
    args = parser.parse_args()
    try:
        saved = migrate(args.approved_sha.strip())
    except MigrationError as exc:
        print(f"RUNTIME_IDENTITY_MIGRATION: BLOCKED: {exc}")
        return 2
    print("RUNTIME_IDENTITY_MIGRATION: PASS")
    print(f"ACTIVE_SHA: {git('rev-parse', 'HEAD')}")
    print(f"CHECKPOINT: {saved}")
    for name in SYSTEM_UNITS:
        print(f"SYSTEM {name}: {'active' if system_active(name) else 'inactive'}")
    for name in USER_UNITS:
        print(f"USER {name}: {'active' if user_active(name) else 'inactive'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
