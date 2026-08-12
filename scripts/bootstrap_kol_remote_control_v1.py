#!/usr/bin/env python3
"""One-time fail-closed bootstrap for KÖL Git baseline + AI PROF registration."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CONTROL = Path("/home/agent/projects/ai-prof-control-center")
KOL = Path("/home/agent/Загрузки/kol-travel-platform")
KOL_REPO = "stvelikiy-star/kol-travel-platform"
PROJECT_ID = "kol-travel-platform"

EXCLUDE_LINES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    "**/.env",
    "**/.env.local",
    "node_modules/",
    ".next/",
    "dist/",
    "build/",
    "coverage/",
    "supabase/.temp/",
    "*.log",
    "*.pem",
    "*.key",
    "credentials*.json",
    "service-account*.json",
)

FORBIDDEN_NAME_PATTERNS = (
    re.compile(r"(^|/)\.env(?:\.|$)", re.I),
    re.compile(r"(^|/)(credentials?|secrets?)(?:[._/-]|$)", re.I),
    re.compile(r"(^|/)(service[-_]?account)(?:[._/-]|$)", re.I),
    re.compile(r"\.(?:pem|key|p12|pfx)$", re.I),
)

KOL_ENTRY = {
    "project_id": PROJECT_ID,
    "path": str(KOL),
    "enabled": True,
    "base_branch": "main",
    "allowed_base_branches": ["main"],
    "work_prefixes": ["feature/", "fix/"],
    "allowed_scope": [
        "README.md",
        "docs/**",
        "src/**",
        "app/**",
        "components/**",
        "lib/**",
        "public/**",
        "tests/**",
        "supabase/**",
        "package.json",
        "tsconfig.json",
        "next.config.mjs",
        "next.config.js",
        "tailwind.config.ts",
        "postcss.config.mjs",
        "middleware.ts",
    ],
    "forbidden_scope": [
        ".git/**",
        ".env",
        "**/.env",
        ".env.local",
        "**/.env.local",
        "secrets",
        "credentials",
        "node_modules/**",
        ".next/**",
        "supabase/.temp/**",
        "package-lock.json",
    ],
    "agent_context": "agents/kol",
    "allow_commits": False,
    "allow_push": False,
    "allow_merge": False,
    "allow_deployment": False,
    "require_clean_repository": True,
    "max_scope_files": 20,
    "code_required_commands": ["git", "python3", "node", "npm", "npx"],
    "code_required_checks": [
        "npx tsc --noEmit",
        "npm run build",
    ],
    "code_toolchain": "nvm-node",
}


class BootstrapError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GH_PROMPT_DISABLED": "1"},
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise BootstrapError(f"{argv[0]} failed: {detail[:1200]}")
    return (result.stdout or "").strip()


def resolved_exact(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BootstrapError(f"{label} may not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError(f"{label} unavailable: {path}: {exc}") from exc
    if resolved != path:
        raise BootstrapError(f"{label} path is not exact: {path} -> {resolved}")
    if not resolved.is_dir():
        raise BootstrapError(f"{label} is not a directory: {resolved}")
    return resolved


def git_ok(path: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def require_control_clean() -> None:
    resolved_exact(CONTROL, "control center")
    if not git_ok(CONTROL):
        raise BootstrapError("control center is not a Git repository")
    branch = run(["git", "branch", "--show-current"], cwd=CONTROL)
    if branch != "main":
        raise BootstrapError(f"control center must be on main, found {branch!r}")
    status = run(["git", "status", "--porcelain"], cwd=CONTROL)
    if status:
        raise BootstrapError("control center working tree is dirty")
    origin = run(["git", "remote", "get-url", "origin"], cwd=CONTROL)
    if "stvelikiy-star/ai-prof-control-center" not in origin:
        raise BootstrapError("control center origin is unexpected")
    run(["git", "fetch", "origin", "main"], cwd=CONTROL)
    local = run(["git", "rev-parse", "HEAD"], cwd=CONTROL)
    remote = run(["git", "rev-parse", "origin/main"], cwd=CONTROL)
    if local != remote:
        raise BootstrapError("control center main is not exactly origin/main")


def require_kol_shape() -> None:
    resolved_exact(KOL, "KÖL project")
    for name in ("package.json", "tsconfig.json"):
        if not (KOL / name).is_file():
            raise BootstrapError(f"KÖL expected file missing: {name}")
    if (KOL / "node_modules").is_symlink() or (KOL / ".next").is_symlink():
        raise BootstrapError("KÖL build directories may not be symlinks")


def gh_repo_exists(repo: str) -> bool:
    result = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "nameWithOwner,isPrivate,defaultBranchRef"],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GH_PROMPT_DISABLED": "1"},
    )
    return result.returncode == 0


def install_local_excludes() -> None:
    info = KOL / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = existing.splitlines()
    changed = False
    for item in EXCLUDE_LINES:
        if item not in lines:
            lines.append(item)
            changed = True
    if changed or not exclude.exists():
        exclude.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def staged_paths() -> list[str]:
    output = run(["git", "diff", "--cached", "--name-only", "-z"], cwd=KOL)
    return [part for part in output.split("\x00") if part]


def validate_staged(paths: list[str]) -> None:
    if not paths:
        raise BootstrapError("KÖL baseline would be empty")
    for rel in paths:
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise BootstrapError(f"unsafe staged path: {rel}")
        for pattern in FORBIDDEN_NAME_PATTERNS:
            if pattern.search(rel):
                raise BootstrapError(f"secret-like file refused from baseline: {rel}")
        full = KOL / rel
        if full.is_symlink():
            raise BootstrapError(f"symlink refused from baseline: {rel}")
        try:
            size = full.stat().st_size
        except OSError as exc:
            raise BootstrapError(f"cannot stat staged path {rel}: {exc}") from exc
        if size > 90 * 1024 * 1024:
            raise BootstrapError(f"file exceeds 90 MiB safety limit: {rel}")


def create_kol_baseline() -> str:
    if git_ok(KOL):
        branch = run(["git", "branch", "--show-current"], cwd=KOL)
        origin = run(["git", "remote", "get-url", "origin"], cwd=KOL, check=False)
        if branch == "main" and "stvelikiy-star/kol-travel-platform" in origin:
            status = run(["git", "status", "--porcelain"], cwd=KOL)
            if status:
                raise BootstrapError("existing KÖL Git worktree is dirty; refusing idempotent reuse")
            if not gh_repo_exists(KOL_REPO):
                raise BootstrapError("KÖL has an origin but the private GitHub repository is unavailable")
            return run(["git", "rev-parse", "HEAD"], cwd=KOL)
        raise BootstrapError("KÖL already has unrelated Git metadata; review required")

    if gh_repo_exists(KOL_REPO):
        raise BootstrapError("GitHub KÖL repository already exists while local KÖL has no valid Git baseline")

    run(["git", "init", "-b", "main"], cwd=KOL)
    install_local_excludes()
    run(["git", "add", "-A"], cwd=KOL)
    paths = staged_paths()
    validate_staged(paths)

    run(["git", "config", "user.name", "stvelikiy-star"], cwd=KOL)
    email = run(["gh", "api", "user", "--jq", ".email // empty"], check=False)
    if not email:
        email = "stvelikiy-star@users.noreply.github.com"
    run(["git", "config", "user.email", email], cwd=KOL)
    run(["git", "commit", "-m", "chore: establish recovered KOL source baseline"], cwd=KOL)
    head = run(["git", "rev-parse", "HEAD"], cwd=KOL)

    run([
        "gh", "repo", "create", KOL_REPO, "--private", "--source", str(KOL),
        "--remote", "origin", "--push",
    ])

    payload = json.loads(
        run(["gh", "repo", "view", KOL_REPO, "--json", "nameWithOwner,isPrivate,defaultBranchRef"])
    )
    if payload.get("nameWithOwner") != KOL_REPO or payload.get("isPrivate") is not True:
        raise BootstrapError("KÖL GitHub repository verification failed")
    default_branch = (payload.get("defaultBranchRef") or {}).get("name")
    if default_branch != "main":
        raise BootstrapError(f"KÖL default branch must be main, found {default_branch!r}")
    remote_head = run(["git", "ls-remote", "origin", "refs/heads/main"], cwd=KOL).split()
    if not remote_head or remote_head[0] != head:
        raise BootstrapError("KÖL remote main does not match local baseline HEAD")
    if run(["git", "status", "--porcelain"], cwd=KOL):
        raise BootstrapError("KÖL working tree is dirty after baseline push")
    return head


def register_kol() -> str:
    registry_path = CONTROL / "orchestrator" / "projects.json"
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    projects = data.get("projects")
    if data.get("version") != 1 or not isinstance(projects, list):
        raise BootstrapError("control center project registry has unexpected structure")

    matches = [item for item in projects if item.get("project_id") == PROJECT_ID]
    if len(matches) > 1:
        raise BootstrapError("duplicate KÖL registry entries detected")
    changed = False
    if matches:
        if matches[0] != KOL_ENTRY:
            raise BootstrapError("existing KÖL registry entry differs from the approved bootstrap contract")
    else:
        projects.append(KOL_ENTRY)
        registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed = True

    sys.path.insert(0, str(CONTROL / "orchestrator"))
    import submit_task  # type: ignore
    loaded = submit_task.read_registry(CONTROL, validate_project=True)
    if PROJECT_ID not in loaded:
        raise BootstrapError("KÖL registry validation did not return the project")

    if changed:
        run(["git", "add", "orchestrator/projects.json"], cwd=CONTROL)
        staged = run(["git", "diff", "--cached", "--name-only"], cwd=CONTROL).splitlines()
        if staged != ["orchestrator/projects.json"]:
            raise BootstrapError(f"unexpected control-center staged files: {staged}")
        run(["git", "commit", "-m", "config: register KOL remote project"], cwd=CONTROL)
        run(["git", "push", "origin", "main"], cwd=CONTROL)

    head = run(["git", "rev-parse", "HEAD"], cwd=CONTROL)
    run(["git", "fetch", "origin", "main"], cwd=CONTROL)
    remote = run(["git", "rev-parse", "origin/main"], cwd=CONTROL)
    if head != remote:
        raise BootstrapError("control center local/remote main diverged after KÖL registration")
    if run(["git", "status", "--porcelain"], cwd=CONTROL):
        raise BootstrapError("control center working tree is dirty after KÖL registration")
    return head


def main() -> int:
    print("[KOL-REMOTE] 1/5 control-center preflight")
    require_control_clean()
    print("[KOL-REMOTE] 2/5 KÖL source preflight")
    require_kol_shape()

    if shutil.which("git") is None or shutil.which("gh") is None:
        raise BootstrapError("required command missing: git and gh are required")
    run(["gh", "auth", "status"])

    print("[KOL-REMOTE] 3/5 creating/verifying private KÖL Git baseline")
    kol_head = create_kol_baseline()

    print("[KOL-REMOTE] 4/5 registering KÖL in AI PROF")
    control_head = register_kol()

    print("[KOL-REMOTE] 5/5 postconditions")
    print("KOL_REMOTE_BOOTSTRAP=PASS")
    print(f"KOL_HEAD={kol_head}")
    print(f"CONTROL_CENTER_HEAD={control_head}")
    print(f"KOL_PATH={KOL}")
    print(f"KOL_REPOSITORY={KOL_REPO}")
    print("SECRETS_STAGED=NO")
    print("DEPLOYMENT_PERFORMED=NO")
    print("DATABASE_CHANGED=NO")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapError as exc:
        print(f"KOL_REMOTE_BOOTSTRAP=BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
