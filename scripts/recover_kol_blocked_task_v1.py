#!/usr/bin/env python3
"""Fail-closed recovery for KÖL after a blocked AI PROF source task.

This tool is intentionally narrow. It never deploys, touches Supabase, deletes
source changes, resets branches, or edits task queues. If the KÖL worktree has
changes, every changed path must be inside the union of the approved issue #59
and #60 source scopes. Those changes are preserved in a Git stash before the
repository is returned to a clean, fast-forwarded main branch.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

KOL_PROJECT = Path("/home/agent/Загрузки/kol-travel-platform")
EXPECTED_ORIGINS = {
    "git@github.com:stvelikiy-star/kol-travel-platform.git",
    "https://github.com/stvelikiy-star/kol-travel-platform.git",
}
ALLOWED_BRANCHES = {
    "main",
    "feature/chatgpt-issue-59",
    "feature/chatgpt-issue-60",
}
ALLOWED_SCOPE = (
    "src/app/partner",
    "src/lib/data/partners.ts",
    "src/lib/data/authenticated-read-utils.ts",
    "src/lib/data/partner-bookings-read.ts",
    "src/lib/data/partner-bookings-supabase.ts",
    "src/lib/data/partner-availability-read.ts",
    "src/lib/data/partner-availability-supabase.ts",
    "src/lib/types/partner-bookings.ts",
    "src/lib/types/partner-availability.ts",
    "src/lib/auth/ownership.ts",
    "src/lib/auth/profile.ts",
    "src/lib/auth/session.ts",
)
FORBIDDEN_PARTS = {
    ".git",
    ".env",
    ".env.local",
    "secrets",
    "credentials",
    "node_modules",
    ".next",
}


class RecoveryBlocked(RuntimeError):
    pass


def run(argv: list[str], *, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=str(KOL_PROJECT),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:1200]
        raise RecoveryBlocked(f"command failed ({argv[0]}): {detail}")
    return result


def git_text(*args: str) -> str:
    return run(["git", *args]).stdout.strip()


def validate_relative(raw: str) -> str:
    if not raw or raw.startswith("/") or "\\" in raw or "\x00" in raw:
        raise RecoveryBlocked(f"unsafe changed path: {raw!r}")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RecoveryBlocked(f"unsafe changed path: {raw!r}")
    if any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in path.parts):
        raise RecoveryBlocked(f"forbidden changed path: {raw}")
    return raw


def nul_paths(payload: str) -> set[str]:
    result: set[str] = set()
    for raw in payload.split("\0"):
        if raw:
            result.add(validate_relative(raw))
    return result


def changed_paths() -> set[str]:
    tracked = run(["git", "diff", "--name-only", "-z", "HEAD", "--"]).stdout
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "-z", "--"]).stdout
    return nul_paths(tracked) | nul_paths(untracked)


def path_is_allowed(relative: str) -> bool:
    candidate = PurePosixPath(relative)
    for scope in ALLOWED_SCOPE:
        scope_path = PurePosixPath(scope)
        if candidate == scope_path:
            return True
        if scope == "src/app/partner" and scope_path in candidate.parents:
            return True
    return False


def preflight() -> tuple[str, set[str]]:
    if KOL_PROJECT.is_symlink() or KOL_PROJECT.resolve() != KOL_PROJECT:
        raise RecoveryBlocked("KÖL path is not the exact non-symlink project path")
    if not (KOL_PROJECT / ".git").is_dir():
        raise RecoveryBlocked("KÖL Git repository is missing")

    origin = git_text("remote", "get-url", "origin")
    if origin not in EXPECTED_ORIGINS:
        raise RecoveryBlocked(f"unexpected KÖL origin: {origin}")

    branch = git_text("branch", "--show-current")
    if branch not in ALLOWED_BRANCHES:
        raise RecoveryBlocked(f"unexpected KÖL branch: {branch}")

    changes = changed_paths()
    unexpected = sorted(path for path in changes if not path_is_allowed(path))
    if unexpected:
        raise RecoveryBlocked("unexpected changes outside approved issue #59/#60 scope: " + ", ".join(unexpected))
    return branch, changes


def preserve_changes(changes: set[str]) -> str:
    if not changes:
        return "none"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    marker = f"ai-prof-kol-blocked-recovery-{stamp}"
    result = run(["git", "stash", "push", "--include-untracked", "-m", marker])
    if changed_paths():
        raise RecoveryBlocked("KÖL worktree is still dirty after recovery stash")
    stash_line = git_text("stash", "list", "-1", "--format=%gd%x09%H%x09%s")
    if marker not in stash_line:
        raise RecoveryBlocked("recovery stash could not be verified")
    return stash_line


def return_to_main() -> str:
    run(["git", "switch", "main"])
    run(["git", "fetch", "--no-tags", "origin", "main"])
    run(["git", "merge", "--ff-only", "origin/main"])
    if git_text("branch", "--show-current") != "main":
        raise RecoveryBlocked("KÖL did not return to main")
    if changed_paths():
        raise RecoveryBlocked("KÖL main is not clean after recovery")
    local_head = git_text("rev-parse", "HEAD")
    remote_head = git_text("rev-parse", "origin/main")
    if local_head != remote_head:
        raise RecoveryBlocked("KÖL main is not exactly origin/main")
    return local_head


def main() -> int:
    try:
        print("[1/5] exact KÖL project/origin")
        branch, changes = preflight()
        print(f"[2/5] branch={branch} changed_paths={len(changes)}")
        stash = preserve_changes(changes)
        print("[3/5] blocked-task changes preserved")
        head = return_to_main()
        print("[4/5] main fast-forwarded and clean")
        print("[5/5] postconditions")
        print("KOL_BLOCKED_RECOVERY=PASS")
        print(f"KOL_HEAD={head}")
        print(f"RECOVERY_STASH={stash}")
        print("SOURCE_CHANGES_DELETED=NO")
        print("DATABASE_CHANGED=NO")
        print("DEPLOYMENT_PERFORMED=NO")
        return 0
    except (RecoveryBlocked, OSError, subprocess.SubprocessError) as exc:
        print(f"KOL_BLOCKED_RECOVERY=BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
