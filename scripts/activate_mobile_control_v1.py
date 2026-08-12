#!/usr/bin/env python3
"""Owner-run staged activation for AI PROF mobile control V1.

This script is intentionally conservative. It updates only the AI PROF Control
Center checkout and its three systemd units. It does not deploy AK BERMET,
apply migrations, edit secrets, reset Git state or clean untracked files.

Run as the normal `agent` user after `sudo -v`; the script invokes sudo only for
systemd/unit-file operations so Git/worktree ownership stays with the user.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIVE = Path("/home/agent/projects/ai-prof-control-center")
BACKUP_ROOT = Path("/home/agent/ai-prof-backups/control-center")
UNIT_DIR = Path("/etc/systemd/system")
SERVICES = (
    "ai-prof-control-center.service",
    "ai-prof-telegram-bridge.service",
    "ai-prof-github-task-gateway.service",
)
EXPECTED_REPOSITORY = "stvelikiy-star/ai-prof-control-center"


class ActivationError(RuntimeError):
    pass


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise ActivationError(
            f"command failed ({argv[0]}): {detail[:1200]}"
        )
    return result


def git(*args: str, capture: bool = True) -> str:
    result = run(["git", *args], cwd=LIVE, capture=capture)
    return (result.stdout or "").strip()


def sudo(*args: str, capture: bool = False, check: bool = True):
    return run(["sudo", "-n", *args], capture=capture, check=check)


def service_active(name: str) -> bool:
    return (
        sudo("systemctl", "is-active", "--quiet", name, check=False).returncode
        == 0
    )


def verify_preconditions(approved_sha: str) -> dict[str, object]:
    if os.geteuid() == 0:
        raise ActivationError("run as the normal agent user, not root")
    if LIVE.is_symlink() or not LIVE.is_dir() or not (LIVE / ".git").exists():
        raise ActivationError("live Control Center checkout is unavailable")
    if shutil.which("git") is None or shutil.which("python3") is None:
        raise ActivationError("git and python3 are required")
    if shutil.which("gh") is None:
        raise ActivationError("GitHub CLI is required for the gateway")
    if not approved_sha or len(approved_sha) != 40:
        raise ActivationError("--approved-sha must be the exact 40-char main SHA")

    status = git("status", "--porcelain")
    if status:
        raise ActivationError(
            "live Control Center worktree is dirty; preserve/reconcile it before activation"
        )

    branch = git("branch", "--show-current")
    head = git("rev-parse", "HEAD")
    origin = git("remote", "get-url", "origin")
    if not origin:
        raise ActivationError("origin remote is missing")

    # Verify GitHub authentication and exact private repository before any stop.
    auth = run(
        [
            "gh",
            "api",
            f"repos/{EXPECTED_REPOSITORY}",
            "--jq",
            'if .private == true then "PRIVATE" else "NOT_PRIVATE" end',
        ],
        capture=True,
    )
    if (auth.stdout or "").strip() != "PRIVATE":
        raise ActivationError("expected private GitHub repository is not accessible")

    git("fetch", "--prune", "origin", "main", capture=False)
    remote_sha = git("rev-parse", "origin/main")
    if remote_sha != approved_sha:
        raise ActivationError(
            f"origin/main moved: expected {approved_sha}, got {remote_sha}"
        )
    if run(
        ["git", "merge-base", "--is-ancestor", head, approved_sha],
        cwd=LIVE,
        check=False,
    ).returncode != 0:
        raise ActivationError(
            "current live HEAD is not an ancestor of approved main; refusing to overwrite local history"
        )

    # Ensure sudo is already authorized so activation cannot stop halfway for a
    # password prompt.
    sudo("true")
    active = {name: service_active(name) for name in SERVICES}
    return {"branch": branch, "head": head, "active": active}


def create_checkpoint(meta: dict[str, object], approved_sha: str) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_ROOT / f"mobile-control-{stamp}"
    if backup.exists() or backup.is_symlink():
        raise ActivationError("backup checkpoint path already exists")
    backup.mkdir(parents=True, mode=0o700)

    (backup / "PREVIOUS_BRANCH").write_text(
        str(meta["branch"]) + "\n", encoding="utf-8"
    )
    (backup / "PREVIOUS_SHA").write_text(
        str(meta["head"]) + "\n", encoding="utf-8"
    )
    (backup / "APPROVED_SHA").write_text(approved_sha + "\n", encoding="utf-8")
    (backup / "git-status.txt").write_text(
        git("status", "--short", "--branch") + "\n", encoding="utf-8"
    )

    run(
        ["git", "bundle", "create", str(backup / "repository.bundle"), "--all"],
        cwd=LIVE,
    )
    for name in SERVICES:
        target = UNIT_DIR / name
        exists = target.exists()
        (backup / f"{name}.existed").write_text(
            "yes\n" if exists else "no\n", encoding="utf-8"
        )
        if exists:
            sudo("cp", "--preserve=mode,timestamps", str(target), str(backup / name))
            sudo("chown", f"{os.getuid()}:{os.getgid()}", str(backup / name))
    return backup


def stop_services() -> None:
    # Gateway may not exist yet; stop is idempotent and failure on a missing unit
    # is ignored only for that pre-activation condition.
    for name in reversed(SERVICES):
        sudo("systemctl", "stop", name, check=False)


def switch_to_approved_main(approved_sha: str) -> None:
    current = git("branch", "--show-current")
    if current == "main":
        git("merge", "--ff-only", approved_sha, capture=False)
    else:
        # main is a local release pointer; source-of-truth is verified origin/main.
        # Updating this local pointer is safe only because approved_sha was proven
        # to be the exact remote main and current live HEAD is its ancestor.
        run(["git", "branch", "-f", "main", approved_sha], cwd=LIVE)
        run(["git", "switch", "main"], cwd=LIVE)
    if git("rev-parse", "HEAD") != approved_sha:
        raise ActivationError("live checkout did not reach approved SHA")
    if git("status", "--porcelain"):
        raise ActivationError("live checkout became dirty after main switch")


def run_release_tests() -> None:
    run(["python3", "test_control_center.py"], cwd=LIVE)
    run(["python3", "-m", "unittest", "-v"], cwd=LIVE)
    run(["python3", "scripts/bootstrap_self_maintenance.py"], cwd=LIVE)


def install_units() -> None:
    for name in SERVICES:
        source = LIVE / "systemd" / name
        if not source.is_file():
            raise ActivationError(f"missing staged systemd unit: {name}")
        sudo("install", "-m", "0644", str(source), str(UNIT_DIR / name))
    sudo("systemctl", "daemon-reload")
    sudo("systemctl", "enable", *SERVICES)


def start_and_verify() -> None:
    # Start core first, then control surfaces.
    for name in SERVICES:
        sudo("systemctl", "restart", name)
    for name in SERVICES:
        if not service_active(name):
            logs = sudo(
                "journalctl",
                "-u",
                name,
                "-n",
                "40",
                "--no-pager",
                capture=True,
                check=False,
            )
            detail = (logs.stdout or logs.stderr or "service inactive").strip()
            raise ActivationError(f"{name} failed health check: {detail[-2000:]}")


def restore_previous_checkout(meta: dict[str, object]) -> None:
    old_branch = str(meta["branch"])
    old_sha = str(meta["head"])
    if git("status", "--porcelain"):
        raise ActivationError("cannot auto-rollback a dirty checkout")
    if old_branch:
        # Move away from main before restoring a previous main pointer.
        run(["git", "switch", "--detach", old_sha], cwd=LIVE)
        run(["git", "branch", "-f", old_branch, old_sha], cwd=LIVE)
        run(["git", "switch", old_branch], cwd=LIVE)
    else:
        run(["git", "switch", "--detach", old_sha], cwd=LIVE)


def restore_units(backup: Path) -> None:
    for name in SERVICES:
        existed = (backup / f"{name}.existed").read_text(encoding="utf-8").strip()
        target = UNIT_DIR / name
        saved = backup / name
        if existed == "yes" and saved.is_file():
            sudo("install", "-m", "0644", str(saved), str(target))
        elif existed == "no":
            sudo("rm", "-f", str(target))
    sudo("systemctl", "daemon-reload")


def rollback(meta: dict[str, object], backup: Path) -> None:
    stop_services()
    restore_units(backup)
    restore_previous_checkout(meta)
    active = meta.get("active", {})
    if isinstance(active, dict):
        for name in SERVICES:
            if active.get(name):
                sudo("systemctl", "restart", name, check=False)


def activate(approved_sha: str) -> Path:
    meta = verify_preconditions(approved_sha)
    backup = create_checkpoint(meta, approved_sha)
    stopped = False
    try:
        stop_services()
        stopped = True
        switch_to_approved_main(approved_sha)
        run_release_tests()
        install_units()
        start_and_verify()
    except Exception as exc:
        if stopped:
            try:
                rollback(meta, backup)
            except Exception as rollback_exc:
                raise ActivationError(
                    f"activation failed: {exc}; automatic rollback ALSO failed: {rollback_exc}; checkpoint={backup}"
                ) from rollback_exc
        if isinstance(exc, ActivationError):
            raise
        raise ActivationError(str(exc)) from exc
    return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-sha", required=True)
    args = parser.parse_args()
    try:
        checkpoint = activate(args.approved_sha.strip())
    except ActivationError as exc:
        print(f"MOBILE_CONTROL_ACTIVATION: BLOCKED: {exc}")
        return 2
    print("MOBILE_CONTROL_ACTIVATION: PASS")
    print(f"ACTIVE_SHA: {git('rev-parse', 'HEAD')}")
    print(f"ROLLBACK_CHECKPOINT: {checkpoint}")
    for name in SERVICES:
        print(f"SERVICE {name}: {'active' if service_active(name) else 'inactive'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
