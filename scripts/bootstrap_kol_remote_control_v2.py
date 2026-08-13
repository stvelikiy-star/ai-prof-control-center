#!/usr/bin/env python3
"""Recovery-safe KÖL bootstrap after the V1 .env.example fail-closed stop.

This wrapper preserves V1 safety boundaries while adding two guarantees:
1. every .env* template/variant is excluded from the Git baseline;
2. an unborn, no-remote Git repository left by a failed V1 run can be resumed
   without deleting source files or weakening validation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap_kol_remote_control_v1 as v1


# V1 intentionally refused every .env* name, but did not exclude .env.example
# before staging. Keep the refusal and broaden the local excludes instead.
v1.EXCLUDE_LINES = tuple(dict.fromkeys((*v1.EXCLUDE_LINES, ".env.*", "**/.env.*")))


def git_success(argv: list[str], *, cwd: Path) -> bool:
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GH_PROMPT_DISABLED": "1"},
    )
    return result.returncode == 0


def has_head() -> bool:
    return git_success(["git", "rev-parse", "--verify", "HEAD"], cwd=v1.KOL)


def remote_names() -> list[str]:
    output = v1.run(["git", "remote"], cwd=v1.KOL, check=False)
    return [line.strip() for line in output.splitlines() if line.strip()]


def stage_fresh_baseline() -> list[str]:
    """Rebuild only the Git index; never remove or alter working-tree files."""
    v1.install_local_excludes()
    v1.run(
        ["git", "rm", "-r", "--cached", "--ignore-unmatch", "."],
        cwd=v1.KOL,
        check=False,
    )
    v1.run(["git", "add", "-A"], cwd=v1.KOL)
    paths = v1.staged_paths()
    v1.validate_staged(paths)
    return paths


def commit_and_publish_baseline() -> str:
    v1.run(["git", "config", "user.name", "stvelikiy-star"], cwd=v1.KOL)
    email = v1.run(["gh", "api", "user", "--jq", ".email // empty"], check=False)
    if not email:
        email = "stvelikiy-star@users.noreply.github.com"
    v1.run(["git", "config", "user.email", email], cwd=v1.KOL)
    v1.run(
        ["git", "commit", "-m", "chore: establish recovered KOL source baseline"],
        cwd=v1.KOL,
    )
    head = v1.run(["git", "rev-parse", "HEAD"], cwd=v1.KOL)

    v1.run([
        "gh", "repo", "create", v1.KOL_REPO, "--private", "--source", str(v1.KOL),
        "--remote", "origin", "--push",
    ])

    payload = json.loads(
        v1.run([
            "gh", "repo", "view", v1.KOL_REPO,
            "--json", "nameWithOwner,isPrivate,defaultBranchRef",
        ])
    )
    if payload.get("nameWithOwner") != v1.KOL_REPO:
        raise v1.BootstrapError("KÖL GitHub repository identity verification failed")
    if payload.get("isPrivate") is not True:
        raise v1.BootstrapError("KÖL GitHub repository is not private")
    default_branch = (payload.get("defaultBranchRef") or {}).get("name")
    if default_branch != "main":
        raise v1.BootstrapError(
            f"KÖL default branch must be main, found {default_branch!r}"
        )

    remote_head = v1.run(
        ["git", "ls-remote", "origin", "refs/heads/main"], cwd=v1.KOL
    ).split()
    if not remote_head or remote_head[0] != head:
        raise v1.BootstrapError("KÖL remote main does not match local baseline HEAD")
    if v1.run(["git", "status", "--porcelain"], cwd=v1.KOL):
        raise v1.BootstrapError("KÖL working tree is dirty after baseline push")
    return head


def create_or_resume_kol_baseline() -> str:
    # Fully established baseline: delegate to V1's strict idempotent verifier.
    if v1.git_ok(v1.KOL):
        branch = v1.run(["git", "branch", "--show-current"], cwd=v1.KOL)
        origin = v1.run(["git", "remote", "get-url", "origin"], cwd=v1.KOL, check=False)
        if branch == "main" and "stvelikiy-star/kol-travel-platform" in origin:
            return v1.create_kol_baseline()

        # Recovery is allowed only for the exact harmless partial state V1 can
        # leave before its first commit: main, no HEAD, no remotes, no GitHub repo.
        if branch != "main":
            raise v1.BootstrapError(
                f"partial KÖL Git repository must be on main, found {branch!r}"
            )
        if has_head():
            raise v1.BootstrapError(
                "KÖL already has local commits but no approved remote; manual review required"
            )
        if remote_names():
            raise v1.BootstrapError(
                "KÖL partial Git repository has a remote; manual review required"
            )
        if v1.gh_repo_exists(v1.KOL_REPO):
            raise v1.BootstrapError(
                "private KÖL GitHub repository already exists while local bootstrap is unborn"
            )
        git_dir = v1.KOL / ".git"
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise v1.BootstrapError("KÖL partial .git metadata is unsafe")

        print("[KOL-REMOTE-V2] resuming verified unborn V1 bootstrap state")
        stage_fresh_baseline()
        return commit_and_publish_baseline()

    if v1.gh_repo_exists(v1.KOL_REPO):
        raise v1.BootstrapError(
            "GitHub KÖL repository already exists while local KÖL has no Git baseline"
        )

    v1.run(["git", "init", "-b", "main"], cwd=v1.KOL)
    stage_fresh_baseline()
    return commit_and_publish_baseline()


def main() -> int:
    print("[KOL-REMOTE-V2] 1/5 control-center preflight")
    v1.require_control_clean()
    print("[KOL-REMOTE-V2] 2/5 KÖL source preflight")
    v1.require_kol_shape()

    if shutil.which("git") is None or shutil.which("gh") is None:
        raise v1.BootstrapError("required command missing: git and gh are required")
    v1.run(["gh", "auth", "status"])

    print("[KOL-REMOTE-V2] 3/5 creating/resuming private KÖL Git baseline")
    kol_head = create_or_resume_kol_baseline()

    print("[KOL-REMOTE-V2] 4/5 registering KÖL in AI PROF")
    control_head = v1.register_kol()

    print("[KOL-REMOTE-V2] 5/5 postconditions")
    print("KOL_REMOTE_BOOTSTRAP=PASS")
    print("BOOTSTRAP_VERSION=2")
    print(f"KOL_HEAD={kol_head}")
    print(f"CONTROL_CENTER_HEAD={control_head}")
    print(f"KOL_PATH={v1.KOL}")
    print(f"KOL_REPOSITORY={v1.KOL_REPO}")
    print("ENV_VARIANTS_STAGED=NO")
    print("SECRETS_STAGED=NO")
    print("SOURCE_FILES_DELETED=NO")
    print("DEPLOYMENT_PERFORMED=NO")
    print("DATABASE_CHANGED=NO")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except v1.BootstrapError as exc:
        print(f"KOL_REMOTE_BOOTSTRAP=BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
