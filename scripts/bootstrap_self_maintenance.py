#!/usr/bin/env python3
"""Create/update the isolated AI PROF self-maintenance worktree safely."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

SOURCE = Path("/home/agent/projects/ai-prof-control-center")
TARGET = Path("/home/agent/projects/ai-prof-control-center-maintenance")
BASE_BRANCH = "maintenance/base"
REMOTE_REF = "origin/main"


class BootstrapError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise BootstrapError(f"command failed: {argv[0]} {argv[1] if len(argv) > 1 else ''}: {detail}")
    return (result.stdout or "").strip()


def ensure_plain_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BootstrapError(f"{label} may not be a symlink")
    try:
        return path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BootstrapError(f"{label} does not exist: {path}") from exc


def current_branch(path: Path) -> str:
    return run(["git", "branch", "--show-current"], cwd=path, capture=True)


def head(path: Path, ref: str = "HEAD") -> str:
    return run(["git", "rev-parse", ref], cwd=path, capture=True)


def status(path: Path) -> str:
    return run(["git", "status", "--porcelain"], cwd=path, capture=True)


def bootstrap(source: Path = SOURCE, target: Path = TARGET) -> str:
    source = ensure_plain_directory(source, "source checkout")
    if not (source / ".git").exists():
        # A linked worktree has a .git file; source is expected to be the primary checkout.
        raise BootstrapError("source checkout is not a Git repository")

    run(["git", "fetch", "origin", "main"], cwd=source)
    remote_sha = head(source, REMOTE_REF)

    if target.exists():
        target = ensure_plain_directory(target, "maintenance checkout")
        if not (target / ".git").exists():
            raise BootstrapError("maintenance checkout exists but is not a Git worktree")
        if status(target):
            raise BootstrapError("maintenance checkout is dirty; refusing to modify it")
        if current_branch(target) != BASE_BRANCH:
            raise BootstrapError(f"maintenance checkout must be on {BASE_BRANCH}")
        run(["git", "merge", "--ff-only", REMOTE_REF], cwd=target)
    else:
        # Recreate only a stale local branch that is not currently checked out.
        existing = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{BASE_BRANCH}"],
            cwd=source,
            check=False,
        ).returncode == 0
        if existing:
            branch_sha = head(source, BASE_BRANCH)
            if branch_sha != remote_sha:
                run(["git", "branch", "-f", BASE_BRANCH, REMOTE_REF], cwd=source)
        else:
            run(["git", "branch", BASE_BRANCH, REMOTE_REF], cwd=source)
        run(["git", "worktree", "add", str(target), BASE_BRANCH], cwd=source)
        target = ensure_plain_directory(target, "maintenance checkout")

    if current_branch(target) != BASE_BRANCH:
        raise BootstrapError("maintenance branch verification failed")
    if status(target):
        raise BootstrapError("maintenance checkout is not clean after bootstrap")
    if head(target) != remote_sha:
        raise BootstrapError("maintenance checkout does not match origin/main")
    return remote_sha


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(SOURCE))
    parser.add_argument("--target", default=str(TARGET))
    args = parser.parse_args()
    try:
        sha = bootstrap(Path(args.source), Path(args.target))
    except BootstrapError as exc:
        print(f"SELF_MAINTENANCE_BOOTSTRAP: BLOCKED: {exc}")
        return 2
    print("SELF_MAINTENANCE_BOOTSTRAP: PASS")
    print(f"MAINTENANCE_SHA: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
