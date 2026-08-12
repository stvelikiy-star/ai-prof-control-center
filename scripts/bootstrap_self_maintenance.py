#!/usr/bin/env python3
"""Create/update the isolated AI PROF self-maintenance checkout safely.

The maintenance checkout is a standalone local clone, not a linked Git
worktree.  This keeps its Git metadata independent from the live Control
Center checkout and satisfies the strict task-intake repository contract.
"""
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
        raise BootstrapError(
            f"command failed: {argv[0]} {argv[1] if len(argv) > 1 else ''}: {detail}"
        )
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


def clone_target(source: Path, target: Path, origin_url: str) -> None:
    run(["git", "clone", origin_url, str(target)], cwd=source.parent)
    run(["git", "switch", "-c", BASE_BRANCH, REMOTE_REF], cwd=target)


def convert_linked_worktree(source: Path, target: Path, origin_url: str) -> None:
    """Replace the old clean linked worktree with an independent clone."""
    target = ensure_plain_directory(target, "maintenance checkout")
    if status(target):
        raise BootstrapError("maintenance checkout is dirty; refusing to convert it")
    if current_branch(target) != BASE_BRANCH:
        raise BootstrapError(f"maintenance checkout must be on {BASE_BRANCH}")
    run(["git", "worktree", "remove", str(target)], cwd=source)
    if target.exists():
        raise BootstrapError("linked maintenance worktree was not removed cleanly")
    clone_target(source, target, origin_url)


def bootstrap(source: Path = SOURCE, target: Path = TARGET) -> str:
    source = ensure_plain_directory(source, "source checkout")
    if not (source / ".git").exists():
        raise BootstrapError("source checkout is not a Git repository")

    run(["git", "fetch", "origin", "main"], cwd=source)
    remote_sha = head(source, REMOTE_REF)
    origin_url = run(["git", "remote", "get-url", "origin"], cwd=source, capture=True)
    if not origin_url:
        raise BootstrapError("source origin remote is unavailable")

    if target.exists():
        target = ensure_plain_directory(target, "maintenance checkout")
        git_marker = target / ".git"
        if git_marker.is_file():
            convert_linked_worktree(source, target, origin_url)
            target = ensure_plain_directory(target, "maintenance checkout")
        elif git_marker.is_dir():
            if status(target):
                raise BootstrapError("maintenance checkout is dirty; refusing to modify it")
            if current_branch(target) != BASE_BRANCH:
                raise BootstrapError(f"maintenance checkout must be on {BASE_BRANCH}")
            target_origin = run(
                ["git", "remote", "get-url", "origin"], cwd=target, capture=True
            )
            if target_origin != origin_url:
                raise BootstrapError("maintenance checkout origin does not match source origin")
            run(["git", "fetch", "origin", "main"], cwd=target)
            run(["git", "merge", "--ff-only", REMOTE_REF], cwd=target)
        else:
            raise BootstrapError("maintenance checkout exists but is not a standalone Git clone")
    else:
        clone_target(source, target, origin_url)
        target = ensure_plain_directory(target, "maintenance checkout")

    if not (target / ".git").is_dir():
        raise BootstrapError("maintenance checkout is not an independent Git clone")
    if current_branch(target) != BASE_BRANCH:
        raise BootstrapError("maintenance branch verification failed")
    if status(target):
        raise BootstrapError("maintenance checkout is not clean after bootstrap")
    run(["git", "fetch", "origin", "main"], cwd=target)
    if head(target) != head(target, REMOTE_REF):
        raise BootstrapError("maintenance checkout does not match origin/main")
    if head(target) != remote_sha:
        raise BootstrapError("maintenance checkout does not match source origin/main")
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
