#!/usr/bin/env python3
"""Trusted KÖL-only publisher for Stage 01C-approved AI PROF tasks.

Security boundary:
- consumes only queue/approved tasks with matching successful Stage 01B/01C logs;
- supports exactly one fixed project/repository pair (KÖL);
- requires the Git diff to remain inside Scope-Files;
- commits/pushes only the exact task work branch;
- opens a PR but never merges, deploys, touches databases, or reads secrets;
- returns the laptop repository to a clean, fast-forwarded main branch.

Task prose is never executed as shell input. All subprocesses use fixed argv lists.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
DEFAULT_STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
KOL_PROJECT = Path("/home/agent/Загрузки/kol-travel-platform")
KOL_REPOSITORY = "stvelikiy-star/kol-travel-platform"
CONTROL_REPOSITORY = "stvelikiy-star/ai-prof-control-center"
KOL_BASE_BRANCH = "main"
OWNER = "stvelikiy-star"
WORK_BRANCH_RE = re.compile(r"^feature/chatgpt-issue-(\d+)$")
SOURCE_MARKER_RE = re.compile(r"Source: authorized private GitHub task issue #(\d+)\.")
FIELD_RE_TEMPLATE = r"(?mi)^[ \t]*{field}:[ \t]*(.*?)[ \t]*$"
TASK_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{5,159}$")
FORBIDDEN_PARTS = {".git", ".env", ".env.local", "secrets", "credentials", "node_modules", ".next"}
LOG_LIMIT = 16000
SELF_TEST_MARKER = "APPROVED_TASK_PUBLISHER_SELF_TEST_PASS"
PASS_MARKER = "APPROVED_TASK_PUBLISHER_PASS"
SECRET_PATTERNS = (
    re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://\S+"),
)


class PublisherError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path
    runtime: Path
    approved: Path
    completed: Path
    logs: Path
    lock: Path


def build_paths(root: Path, state_root: Path) -> Paths:
    runtime = state_root.resolve()
    result = Paths(
        root=root.resolve(),
        runtime=runtime,
        approved=runtime / "queue" / "approved",
        completed=runtime / "queue" / "completed",
        logs=runtime / "logs" / "orchestrator",
        lock=runtime / "run" / "approved-task-publisher.lock",
    )
    for directory in (result.approved, result.completed, result.logs, result.lock.parent):
        directory.mkdir(parents=True, exist_ok=True)
    return result


def acquire_lock(path: Path):
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise
    return handle


def redact(text: object) -> str:
    value = str(text)
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def field(text: str, name: str) -> str:
    match = re.search(FIELD_RE_TEMPLATE.format(field=re.escape(name)), text)
    return match.group(1).strip() if match else ""


def validate_relative_path(raw: str) -> str:
    if not raw or raw != raw.strip() or "\x00" in raw or "\r" in raw or "\n" in raw:
        raise PublisherError(f"unsafe relative path: {raw!r}")
    if raw.startswith("/") or "\\" in raw:
        raise PublisherError(f"unsafe relative path: {raw!r}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PublisherError(f"unsafe relative path: {raw!r}")
    if any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in parts):
        raise PublisherError(f"forbidden path in approved publish scope: {raw}")
    return raw


def parse_scope_files(text: str) -> list[str]:
    raw = field(text, "Scope-Files")
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    if not entries or len(entries) > 20:
        raise PublisherError("approved task has invalid Scope-Files")
    result: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        validate_relative_path(entry)
        if entry in seen:
            raise PublisherError(f"duplicate Scope-File: {entry}")
        seen.add(entry)
        result.append(entry)
    return result


def parse_source_issue(text: str, work_branch: str) -> int:
    branch_match = WORK_BRANCH_RE.fullmatch(work_branch)
    markers = SOURCE_MARKER_RE.findall(text)
    if not branch_match or len(markers) != 1:
        raise PublisherError("approved task has invalid GitHub source evidence")
    branch_issue = int(branch_match.group(1))
    marker_issue = int(markers[0])
    if branch_issue != marker_issue:
        raise PublisherError("work branch and source issue do not match")
    return branch_issue


def run(argv: list[str], *, cwd: Path | None = None, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        detail = redact(result.stderr or result.stdout).strip()[:1200]
        raise PublisherError(f"command failed ({argv[0]}): {detail}")
    return result


def git_text(project: Path, *args: str) -> str:
    return run(["git", *args], cwd=project).stdout.strip()


def nul_paths(payload: str) -> set[str]:
    result: set[str] = set()
    for raw in payload.split("\0"):
        if raw:
            validate_relative_path(raw)
            result.add(raw)
    return result


def changed_paths(project: Path) -> set[str]:
    tracked = run(["git", "diff", "--name-only", "-z", "HEAD", "--"], cwd=project).stdout
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "-z", "--"], cwd=project).stdout
    return nul_paths(tracked) | nul_paths(untracked)


def staged_paths(project: Path) -> set[str]:
    return nul_paths(run(["git", "diff", "--cached", "--name-only", "-z", "--"], cwd=project).stdout)


def path_in_scope(project: Path, relative: str, scopes: list[str]) -> bool:
    candidate = PurePosixPath(relative)
    for scope in scopes:
        scope_path = PurePosixPath(scope)
        if candidate == scope_path:
            return True
        if (project / scope).is_dir() and scope_path in candidate.parents:
            return True
    return False


def validate_changes_in_scope(project: Path, paths: set[str], scopes: list[str]) -> None:
    if not paths:
        raise PublisherError("approved task has no Git changes to publish")
    for relative in sorted(paths):
        if not path_in_scope(project, relative, scopes):
            raise PublisherError(f"change outside approved Scope-Files: {relative}")


def latest_stage_log(paths: Paths, task_id: str, stage: str) -> Path:
    candidates = list(paths.logs.glob(f"{task_id}-{stage}-*.log"))
    if not candidates:
        raise PublisherError(f"missing {stage} audit log")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def require_audit_evidence(paths: Paths, task_id: str) -> None:
    text_b = latest_stage_log(paths, task_id, "01B").read_text(encoding="utf-8", errors="replace")
    text_c = latest_stage_log(paths, task_id, "01C").read_text(encoding="utf-8", errors="replace")
    if not (text_b.startswith("STAGE_01B_CODEX_PASS\n") or text_b.startswith("STAGE_01B_CLAUDE_PASS\n")):
        raise PublisherError("latest Stage 01B evidence is not PASS")
    if not text_c.startswith("STAGE_01C_AUDIT_PASS\n"):
        raise PublisherError("latest Stage 01C evidence is not PASS")


def atomic_move_no_replace(source: Path, destination_dir: Path) -> Path:
    destination = destination_dir / source.name
    if destination.exists():
        raise PublisherError(f"destination queue file already exists: {destination}")
    os.rename(source, destination)
    return destination


def write_log(paths: Paths, task_id: str, body: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = paths.logs / f"{task_id}-PUBLISH-{stamp}.log"
    fd, temp_name = tempfile.mkstemp(prefix=".publisher-", dir=paths.logs)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(redact(body)[:LOG_LIMIT].rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def remote_branch_sha(project: Path, branch: str) -> str:
    line = run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], cwd=project).stdout.strip()
    if not line:
        return ""
    parts = line.split()
    if len(parts) != 2 or parts[1] != f"refs/heads/{branch}":
        raise PublisherError("unexpected remote branch evidence")
    return parts[0]


def current_open_pr(branch: str) -> dict | None:
    result = run(["gh", "api", "-X", "GET", f"repos/{KOL_REPOSITORY}/pulls?state=open&base={KOL_BASE_BRANCH}&head={OWNER}:{branch}"])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PublisherError("GitHub returned invalid PR lookup JSON") from exc
    if not isinstance(payload, list) or len(payload) > 1:
        raise PublisherError("GitHub returned invalid PR lookup payload")
    return payload[0] if payload else None


def create_pr(branch: str, task_id: str, goal: str) -> dict:
    title_goal = re.sub(r"[\r\n]+", " ", goal).strip()[:100] or task_id
    body = (
        f"AI PROF approved task `{task_id}`.\n\n"
        "- Stage 01B implementation/checks: PASS\n"
        "- Stage 01C independent Codex audit: PASS\n"
        "- publish scope revalidated before commit\n\n"
        "No automatic merge, deployment, database mutation, or secret access."
    )
    result = run([
        "gh", "api", "-X", "POST", f"repos/{KOL_REPOSITORY}/pulls",
        "-f", f"title=AI PROF approved: {title_goal}",
        "-f", f"head={branch}", "-f", f"base={KOL_BASE_BRANCH}", "-f", f"body={body}",
    ])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PublisherError("GitHub returned invalid PR create JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("html_url"), str):
        raise PublisherError("GitHub did not return a valid PR")
    return payload


def post_source_comment(issue: int, task_id: str, commit_sha: str, pr_url: str) -> None:
    body = (
        "AI PROF approved task published\n"
        f"Task-ID: {task_id}\nCommit: {commit_sha}\nPR: {pr_url}\n"
        "Merge: not performed\nDeployment: not performed\nDatabase: unchanged"
    )
    run(["gh", "api", "-X", "POST", f"repos/{CONTROL_REPOSITORY}/issues/{issue}/comments", "-f", f"body={body}"])


def validate_supported_task(task_text: str) -> tuple[str, str, int, list[str]]:
    task_id = field(task_text, "Task-ID")
    project_path = field(task_text, "Project-Path")
    base_branch = field(task_text, "Base-Branch")
    work_branch = field(task_text, "Work-Branch")
    if not TASK_ID_RE.fullmatch(task_id):
        raise PublisherError("approved task has invalid Task-ID")
    if Path(project_path) != KOL_PROJECT or base_branch != KOL_BASE_BRANCH:
        raise PublisherError("approved task is outside fixed KÖL publishing authority")
    issue = parse_source_issue(task_text, work_branch)
    return task_id, work_branch, issue, parse_scope_files(task_text)


def commit_approved_change(project: Path, task_id: str, work_branch: str, scopes: list[str]) -> str:
    if git_text(project, "branch", "--show-current") != work_branch:
        raise PublisherError("approved task branch mismatch")
    paths = changed_paths(project)
    validate_changes_in_scope(project, paths, scopes)
    run(["git", "add", "-A", "--", *sorted(paths)], cwd=project)
    if staged_paths(project) != paths:
        raise PublisherError("staged path set differs from approved change set")
    run(["git", "-c", "commit.gpgSign=false", "commit", "--no-gpg-sign", "-m", f"ai-prof: approved task {task_id}"], cwd=project)
    commit_sha = git_text(project, "rev-parse", "HEAD")
    commit_paths = nul_paths(run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit_sha], cwd=project).stdout)
    if commit_paths != paths:
        raise PublisherError("created commit does not match approved change set")
    validate_changes_in_scope(project, commit_paths, scopes)
    if changed_paths(project):
        raise PublisherError("working tree is not clean after approved commit")
    return commit_sha


def push_approved_branch(project: Path, branch: str, commit_sha: str) -> None:
    remote_sha = remote_branch_sha(project, branch)
    if remote_sha:
        if remote_sha != commit_sha:
            raise PublisherError("remote approved branch already exists at a different commit")
        return
    run(["git", "push", "--set-upstream", "origin", f"refs/heads/{branch}:refs/heads/{branch}"], cwd=project, timeout=180)
    if remote_branch_sha(project, branch) != commit_sha:
        raise PublisherError("remote branch SHA mismatch after push")


def return_to_clean_main(project: Path) -> None:
    if changed_paths(project):
        raise PublisherError("repository dirty before returning to main")
    run(["git", "switch", KOL_BASE_BRANCH], cwd=project)
    run(["git", "fetch", "--no-tags", "origin", KOL_BASE_BRANCH], cwd=project, timeout=180)
    run(["git", "merge", "--ff-only", f"origin/{KOL_BASE_BRANCH}"], cwd=project)
    if git_text(project, "branch", "--show-current") != KOL_BASE_BRANCH or changed_paths(project):
        raise PublisherError("failed to return repository to clean main")


def sync_clean_main(project: Path) -> bool:
    if not project.is_dir() or not (project / ".git").is_dir():
        return False
    try:
        if git_text(project, "branch", "--show-current") != KOL_BASE_BRANCH or changed_paths(project):
            return False
        run(["git", "fetch", "--no-tags", "origin", KOL_BASE_BRANCH], cwd=project, timeout=180)
        run(["git", "merge", "--ff-only", f"origin/{KOL_BASE_BRANCH}"], cwd=project)
        return not changed_paths(project)
    except PublisherError:
        return False


def process_task(paths: Paths, task: Path) -> int:
    task_text = task.read_text(encoding="utf-8", errors="strict")
    task_id, work_branch, source_issue, scopes = validate_supported_task(task_text)
    require_audit_evidence(paths, task_id)
    project = KOL_PROJECT.resolve(strict=True)
    if project != KOL_PROJECT or not (project / ".git").is_dir():
        raise PublisherError("fixed KÖL project path is unavailable or redirected")
    commit_sha = commit_approved_change(project, task_id, work_branch, scopes)
    push_approved_branch(project, work_branch, commit_sha)
    pr = current_open_pr(work_branch) or create_pr(work_branch, task_id, field(task_text, "Goal"))
    pr_url = pr.get("html_url")
    if not isinstance(pr_url, str) or not pr_url.startswith("https://github.com/"):
        raise PublisherError("approved PR URL is invalid")
    post_source_comment(source_issue, task_id, commit_sha, pr_url)
    return_to_clean_main(project)
    atomic_move_no_replace(task, paths.completed)
    write_log(paths, task_id, "\n".join([
        PASS_MARKER, f"task_id={task_id}", f"work_branch={work_branch}", f"commit={commit_sha}", f"pr={pr_url}",
        "stage_01b=PASS", "stage_01c=PASS", "scope_revalidated=true", "merge_performed=false",
        "deployment_performed=false", "database_changed=false", "secrets_accessed=false", "local_branch=main", "local_worktree_clean=true",
    ]))
    print(PASS_MARKER)
    print(f"task_id={task_id}")
    print(f"pr={pr_url}")
    return 0


def process_one(paths: Paths) -> int:
    supported: list[Path] = []
    for task in sorted(paths.approved.glob("*.md")):
        try:
            text = task.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            continue
        if field(text, "Project-Path") == str(KOL_PROJECT):
            supported.append(task)
    if supported:
        task = supported[0]
        try:
            return process_task(paths, task)
        except Exception as exc:
            write_log(paths, task.stem, f"APPROVED_TASK_PUBLISHER_BLOCKED\n{type(exc).__name__}: {redact(exc)}")
            print(f"APPROVED_TASK_PUBLISHER_BLOCKED: {redact(exc)}", file=sys.stderr)
            return 1
    print("APPROVED_TASK_PUBLISHER_IDLE_MAIN_SYNCED" if sync_clean_main(KOL_PROJECT) else "APPROVED_TASK_PUBLISHER_IDLE")
    return 0


def run_self_test() -> int:
    sample = "\n".join([
        "Task-ID: KOL_TRAVEL_PLATFORM_20260813T040625Z_14EB7E",
        "Project-Path: /home/agent/Загрузки/kol-travel-platform", "Base-Branch: main",
        "Work-Branch: feature/chatgpt-issue-50", "Goal: smoke",
        "Instructions: Source: authorized private GitHub task issue #50.",
        "Scope-Files: docs/a.md, src/lib/a.ts",
    ])
    task_id, branch, issue, scopes = validate_supported_task(sample)
    assert task_id.endswith("14EB7E") and branch.endswith("50") and issue == 50
    assert scopes == ["docs/a.md", "src/lib/a.ts"]
    for bad in ("../x", "/etc/passwd", r"docs\x", ".env", "docs/../../x"):
        try:
            validate_relative_path(bad)
        except PublisherError:
            pass
        else:
            raise RuntimeError(f"unsafe path accepted: {bad}")
    try:
        parse_source_issue(sample.replace("issue #50", "issue #49"), "feature/chatgpt-issue-50")
    except PublisherError:
        pass
    else:
        raise RuntimeError("source issue mismatch accepted")
    print(SELF_TEST_MARKER)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--state-root", default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    paths = build_paths(Path(args.root).resolve(), Path(args.state_root))
    try:
        lock = acquire_lock(paths.lock)
    except BlockingIOError:
        print("APPROVED_TASK_PUBLISHER_ALREADY_RUNNING", file=sys.stderr)
        return 2
    with lock:
        return run_self_test() if args.self_test else process_one(paths)


if __name__ == "__main__":
    raise SystemExit(main())
