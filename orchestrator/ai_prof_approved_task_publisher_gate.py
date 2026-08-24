#!/usr/bin/env python3
"""Fail-closed, commit-only publisher for AI PROF self-maintenance.

Slice 5A grants exactly one new capability: an approved AI PROF task may create
one local commit on its exact work branch. Network publication, push, PR,
merge, deployment, queue movement and completion remain outside this module.

The branch ref is advanced atomically with an expected old SHA. A retry may
only resume the exact single commit already produced for the same task, base
SHA and scoped paths.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

PROJECT_ID = "ai-prof-control-center"
PROJECT_PATH = "/home/agent/projects/ai-prof-control-center-maintenance"
BASE_BRANCH = "maintenance/base"
AGENT_CONTEXT = "agents/ai-prof-control-center"
SOURCE_REPOSITORY = "stvelikiy-star/ai-prof-control-center"
WORK_BRANCH_RE = re.compile(r"(?:feature|fix)/chatgpt-issue-([1-9][0-9]*)\Z")
TASK_ID_RE = re.compile(r"[A-Z0-9][A-Z0-9_-]{5,159}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
MAX_METADATA_BYTES = 256 * 1024
MAX_TASK_BYTES = 1024 * 1024
MAX_SCOPE_FILES = 20
MAX_CANDIDATES = 32
COMMIT_SUBJECT_PREFIX = "ai-prof: approved task "
STAGE_01B_PASS_PREFIXES = ("STAGE_01B_CODEX_PASS\n", "STAGE_01B_CLAUDE_PASS\n")
STAGE_01C_PASS_PREFIX = "STAGE_01C_AUDIT_PASS\n"
NON_COMMIT_CAPABILITIES = ("allow_push", "allow_merge", "allow_deployment")
FORBIDDEN_PARTS = {
    ".git", ".env", ".env.local", "secrets", "credentials",
    "runtime", "state", "queue",
}

class MetadataError(ValueError):
    """Validated metadata cannot prove exact commit-only authority."""

class CommitBlocked(RuntimeError):
    """Repository evidence does not permit the exact local commit."""

@dataclass(frozen=True)
class CommitAuthorization:
    task_id: str
    work_branch: str
    base_sha: str
    scope_files: tuple[str, ...]

@dataclass(frozen=True)
class PublicationDecision:
    decision: str
    reason: str
    task_id: str | None = None
    project_id: str = PROJECT_ID
    commit_sha: str | None = None
    committed: bool = False
    published: bool = False
    complete: bool = False

    def __post_init__(self) -> None:
        if self.published or self.complete:
            raise ValueError("Slice 5A cannot publish or complete a task")
        if self.decision not in {
            "OWNER_ACTION_REQUIRED", "COMMIT_AUTHORIZED", "COMMITTED", "BLOCKED"
        }:
            raise ValueError("unknown AI PROF commit decision")
        if self.decision == "COMMITTED":
            if not self.committed or not isinstance(self.commit_sha, str):
                raise ValueError("COMMITTED requires an exact commit SHA")
            if SHA_RE.fullmatch(self.commit_sha) is None:
                raise ValueError("invalid commit SHA")
        elif self.committed or self.commit_sha is not None:
            raise ValueError("non-committed decision cannot carry a commit")

def _decision(decision: str, reason: str, task_id: object = None,
              *, commit_sha: str | None = None) -> PublicationDecision:
    safe_task = (
        task_id if isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id)
        else None
    )
    return PublicationDecision(
        decision=decision, reason=reason, task_id=safe_task,
        commit_sha=commit_sha, committed=decision == "COMMITTED",
    )

def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MetadataError(f"{name}_malformed")
    return value

def _required_string(metadata: Mapping[str, object], key: str, reason: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value or len(value) > 512:
        raise MetadataError(reason)
    return value

def _exact_string(metadata: Mapping[str, object], key: str,
                  expected: str, reason: str) -> None:
    if metadata.get(key) != expected:
        raise MetadataError(reason)

def _sha(metadata: Mapping[str, object], key: str, reason: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise MetadataError(reason)
    return value

def _source_issue(task: Mapping[str, object]) -> int:
    source = _mapping(task.get("source"), "source")
    _exact_string(source, "kind", "github_issue", "wrong_source_identity")
    _exact_string(source, "repository", SOURCE_REPOSITORY, "wrong_source_identity")
    issue = source.get("issue")
    if type(issue) is not int or issue <= 0:
        raise MetadataError("wrong_source_identity")
    return issue

def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise MetadataError("invalid_scope")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise MetadataError("invalid_scope")
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        raise MetadataError("forbidden_scope")
    return value

def _scope_files(task: Mapping[str, object]) -> tuple[str, ...]:
    raw = task.get("scope_files")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_SCOPE_FILES:
        raise MetadataError("invalid_scope")
    values = tuple(_relative_path(value) for value in raw)
    if len(set(values)) != len(values) or tuple(sorted(values)) != values:
        raise MetadataError("invalid_scope")
    return values

def _matches_any(path: str, patterns: object) -> bool:
    if not isinstance(patterns, list) or not patterns:
        return False
    return any(
        isinstance(pattern, str)
        and (path == pattern or fnmatch.fnmatchcase(path, pattern)
             or PurePosixPath(path).match(pattern))
        for pattern in patterns
    )

def _validate_profile(profile: Mapping[str, object],
                      scope_files: tuple[str, ...]) -> None:
    _exact_string(profile, "project_id", PROJECT_ID, "wrong_profile_identity")
    _exact_string(profile, "path", PROJECT_PATH, "wrong_profile_identity")
    _exact_string(profile, "base_branch", BASE_BRANCH, "wrong_profile_identity")
    _exact_string(profile, "agent_context", AGENT_CONTEXT, "wrong_profile_identity")
    if profile.get("allowed_base_branches") != [BASE_BRANCH]:
        raise MetadataError("wrong_profile_identity")
    if profile.get("allow_commits") is not True:
        raise MetadataError("commit_capability_disabled")
    for flag in NON_COMMIT_CAPABILITIES:
        if profile.get(flag) is not False:
            raise MetadataError("unexpected_capability_state")
    if profile.get("require_clean_repository") is not True:
        raise MetadataError("unexpected_capability_state")
    allowed = profile.get("allowed_scope")
    forbidden = profile.get("forbidden_scope")
    for path in scope_files:
        if not _matches_any(path, allowed) or _matches_any(path, forbidden):
            raise MetadataError("forbidden_scope")

def _validate_task(task: Mapping[str, object]) -> tuple[str, str, tuple[str, ...]]:
    if task.get("lifecycle_state") != "APPROVED":
        raise MetadataError("task_not_approved")
    task_id = _required_string(task, "task_id", "ambiguous_task_identity")
    if TASK_ID_RE.fullmatch(task_id) is None:
        raise MetadataError("ambiguous_task_identity")
    _exact_string(task, "project_id", PROJECT_ID, "wrong_project")
    _exact_string(task, "project_path", PROJECT_PATH, "wrong_project")
    _exact_string(task, "base_branch", BASE_BRANCH, "wrong_branch_identity")
    _exact_string(task, "agent_context", AGENT_CONTEXT, "wrong_profile_identity")
    work_branch = _required_string(task, "work_branch", "wrong_branch_identity")
    match = WORK_BRANCH_RE.fullmatch(work_branch)
    if match is None:
        raise MetadataError("wrong_branch_identity")
    if int(match.group(1)) != _source_issue(task):
        raise MetadataError("wrong_source_identity")
    return task_id, work_branch, _scope_files(task)

def _validate_repository(repository: Mapping[str, object], work_branch: str,
                         scope_files: tuple[str, ...]) -> str:
    _exact_string(repository, "project_id", PROJECT_ID, "wrong_repository_identity")
    _exact_string(repository, "path", PROJECT_PATH, "wrong_repository_identity")
    _exact_string(repository, "base_branch", BASE_BRANCH, "wrong_branch_identity")
    _exact_string(repository, "work_branch", work_branch, "wrong_branch_identity")
    base_sha = _sha(repository, "base_sha", "missing_base_sha")
    observed = repository.get("changed_paths")
    if not isinstance(observed, list) or tuple(observed) != scope_files:
        raise MetadataError("candidate_scope_mismatch")
    return base_sha

def authorize_commit(task: object, profile: object,
                     repository: object) -> CommitAuthorization:
    checked_task = _mapping(task, "task")
    task_id, work_branch, scope_files = _validate_task(checked_task)
    _validate_profile(_mapping(profile, "profile"), scope_files)
    base_sha = _validate_repository(
        _mapping(repository, "repository"), work_branch, scope_files
    )
    return CommitAuthorization(task_id, work_branch, base_sha, scope_files)

def evaluate_publication_authority(task: object, profile: object,
                                   repository: object) -> PublicationDecision:
    task_id = task.get("task_id") if isinstance(task, Mapping) else None
    try:
        authorization = authorize_commit(task, profile, repository)
    except MetadataError as exc:
        return _decision("OWNER_ACTION_REQUIRED", str(exc), task_id)
    return _decision("COMMIT_AUTHORIZED", "commit_only_authorized",
                     authorization.task_id)

def evaluate_approved_tasks(tasks: object, profile: object,
                            repository: object) -> PublicationDecision:
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes, bytearray)):
        return _decision("OWNER_ACTION_REQUIRED", "approved_tasks_malformed")
    if len(tasks) > MAX_CANDIDATES:
        return _decision("OWNER_ACTION_REQUIRED", "ambiguous_approved_task")
    eligible: list[Mapping[str, object]] = []
    for candidate in tasks:
        if not isinstance(candidate, Mapping):
            return _decision("OWNER_ACTION_REQUIRED", "approved_tasks_malformed")
        try:
            _validate_task(candidate)
        except MetadataError:
            continue
        eligible.append(candidate)
    if len(eligible) != 1:
        reason = "approved_task_not_found" if not eligible else "ambiguous_approved_task"
        return _decision("OWNER_ACTION_REQUIRED", reason)
    return evaluate_publication_authority(eligible[0], profile, repository)

def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=120, check=False, shell=False,
    )
    if result.returncode != 0:
        operation = argv[1] if len(argv) > 1 else "unknown"
        raise CommitBlocked(f"git_command_failed:{operation}")
    return result

def _git(project: Path, *args: str) -> str:
    return _run(["git", *args], cwd=project).stdout.strip()

def _nul_paths(payload: str) -> tuple[str, ...]:
    values = tuple(sorted(value for value in payload.split("\0") if value))
    for value in values:
        try:
            _relative_path(value)
        except MetadataError as exc:
            raise CommitBlocked(str(exc)) from exc
    return values

def _changed_paths(project: Path) -> tuple[str, ...]:
    values: set[str] = set()
    commands = (
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    for command in commands:
        values.update(_nul_paths(_git(project, *command)))
    return tuple(sorted(values))

def _staged_paths(project: Path) -> tuple[str, ...]:
    return _nul_paths(_git(project, "diff", "--cached", "--name-only", "-z"))

def _commit_paths(project: Path, commit_sha: str) -> tuple[str, ...]:
    return _nul_paths(_git(
        project, "diff-tree", "--no-commit-id", "--name-only",
        "-r", "-z", commit_sha
    ))

def _verify_exact_commit(project: Path, authorization: CommitAuthorization,
                         commit_sha: str) -> None:
    if SHA_RE.fullmatch(commit_sha) is None:
        raise CommitBlocked("invalid_created_commit")
    parent = _git(project, "rev-parse", f"{commit_sha}^")
    subject = _git(project, "log", "-1", "--format=%s", commit_sha)
    if parent != authorization.base_sha:
        raise CommitBlocked("commit_parent_mismatch")
    if subject != COMMIT_SUBJECT_PREFIX + authorization.task_id:
        raise CommitBlocked("commit_subject_mismatch")
    if _commit_paths(project, commit_sha) != authorization.scope_files:
        raise CommitBlocked("commit_scope_mismatch")
    if _changed_paths(project):
        raise CommitBlocked("working_tree_not_clean_after_commit")

def commit_approved_change(project: Path,
                           authorization: CommitAuthorization) -> str:
    try:
        resolved = project.resolve(strict=True)
    except OSError as exc:
        raise CommitBlocked("project_unavailable") from exc
    if resolved != project or not (project / ".git").is_dir():
        raise CommitBlocked("project_identity_mismatch")
    if Path(_git(project, "rev-parse", "--show-toplevel")) != project:
        raise CommitBlocked("project_identity_mismatch")
    if _git(project, "branch", "--show-current") != authorization.work_branch:
        raise CommitBlocked("work_branch_mismatch")
    if _git(project, "rev-parse", BASE_BRANCH) != authorization.base_sha:
        raise CommitBlocked("base_sha_drift")

    head = _git(project, "rev-parse", "HEAD")
    if head != authorization.base_sha:
        if _changed_paths(project):
            raise CommitBlocked("candidate_and_head_drift")
        _verify_exact_commit(project, authorization, head)
        return head

    if _changed_paths(project) != authorization.scope_files:
        raise CommitBlocked("candidate_scope_mismatch")
    _run(["git", "add", "-A", "--", *authorization.scope_files], cwd=project)
    if _staged_paths(project) != authorization.scope_files:
        raise CommitBlocked("staged_scope_mismatch")
    if _git(project, "rev-parse", "HEAD") != authorization.base_sha:
        raise CommitBlocked("base_sha_compare_and_swap_failed")

    tree_sha = _git(project, "write-tree")
    commit_sha = _git(
        project, "-c", "commit.gpgSign=false", "commit-tree", tree_sha,
        "-p", authorization.base_sha,
        "-m", COMMIT_SUBJECT_PREFIX + authorization.task_id,
    )
    ref = f"refs/heads/{authorization.work_branch}"
    _run(["git", "update-ref", ref, commit_sha, authorization.base_sha],
         cwd=project)
    if _git(project, "rev-parse", "HEAD") != commit_sha:
        raise CommitBlocked("commit_ref_verification_failed")
    _verify_exact_commit(project, authorization, commit_sha)
    return commit_sha

def _read_json_object(path: Path, boundary: Path) -> Mapping[str, object]:
    resolved_boundary = boundary.resolve(strict=True)
    if path.is_symlink():
        raise MetadataError("metadata_symlink_rejected")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(resolved_boundary)
    except ValueError as exc:
        raise MetadataError("metadata_outside_boundary") from exc
    if not resolved.is_file() or resolved.stat().st_size > MAX_METADATA_BYTES:
        raise MetadataError("metadata_unavailable")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise MetadataError("metadata_ambiguous")
            result[key] = value
        return result
    try:
        decoded = json.loads(
            resolved.read_text(encoding="utf-8"), object_pairs_hook=unique_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MetadataError("metadata_malformed") from exc
    return _mapping(decoded, "metadata")

def _load_profile(root: Path) -> Mapping[str, object]:
    payload = _read_json_object(root / "orchestrator/projects.json", root)
    projects = payload.get("projects")
    if not isinstance(projects, list):
        raise MetadataError("profile_missing_or_malformed")
    matching = [
        item for item in projects
        if isinstance(item, Mapping) and item.get("project_id") == PROJECT_ID
    ]
    if len(matching) != 1:
        raise MetadataError("profile_missing_or_ambiguous")
    return matching[0]

def _task_field(text: str, name: str, *, required: bool = True) -> str | None:
    matches = re.findall(
        rf"(?mi)^[ \t]*{re.escape(name)}:[ \t]*(.*?)[ \t]*$", text
    )
    if not matches:
        if required:
            raise MetadataError("approved_task_malformed")
        return None
    if len(matches) != 1 or not matches[0]:
        raise MetadataError("approved_task_ambiguous")
    return matches[0]

def _task_scope(text: str) -> tuple[str, ...]:
    raw = _task_field(text, "Scope-Files")
    assert raw is not None
    values = tuple(_relative_path(item.strip()) for item in raw.split(","))
    if (
        not values or len(values) > MAX_SCOPE_FILES
        or len(set(values)) != len(values) or tuple(sorted(values)) != values
    ):
        raise MetadataError("invalid_scope")
    return values

def _read_approved_task(path: Path, approved_root: Path) -> Mapping[str, object] | None:
    if path.is_symlink():
        raise MetadataError("approved_task_symlink_rejected")
    try:
        resolved_root = approved_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        stat = resolved.stat()
        if not resolved.is_file() or stat.st_size > MAX_TASK_BYTES:
            raise MetadataError("approved_task_unavailable")
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise MetadataError("approved_task_unavailable") from exc

    publication_action = _task_field(
        text, "Publication-Action", required=False
    )
    if publication_action is None:
        return None
    if publication_action != "commit":
        raise MetadataError("unsupported_publication_action")
    if _task_field(text, "Publication-Contract-Version") != "3":
        raise MetadataError("wrong_publication_contract")
    if _task_field(text, "Publication-Repository") != SOURCE_REPOSITORY:
        raise MetadataError("wrong_source_identity")

    issue_text = _task_field(text, "Publication-Source-Issue")
    assert issue_text is not None
    if not issue_text.isascii() or not issue_text.isdigit() or int(issue_text) <= 0:
        raise MetadataError("wrong_source_identity")
    issue = int(issue_text)

    task_id = _task_field(text, "Task-ID")
    assert task_id is not None
    if TASK_ID_RE.fullmatch(task_id) is None or path.stem != task_id:
        raise MetadataError("ambiguous_task_identity")
    work_branch = _task_field(text, "Work-Branch")
    assert work_branch is not None
    if work_branch != f"feature/chatgpt-issue-{issue}":
        raise MetadataError("wrong_branch_identity")
    if _task_field(text, "Execution-Mode") != "code":
        raise MetadataError("wrong_execution_mode")
    if _task_field(text, "Operation-Profile") != "none":
        raise MetadataError("wrong_operation_profile")
    if _task_field(text, "Project-Path") != PROJECT_PATH:
        raise MetadataError("wrong_project")
    if _task_field(text, "Base-Branch") != BASE_BRANCH:
        raise MetadataError("wrong_branch_identity")
    if _task_field(text, "Agent-Context") != AGENT_CONTEXT:
        raise MetadataError("wrong_profile_identity")

    return {
        "task_id": task_id,
        "lifecycle_state": "APPROVED",
        "project_id": PROJECT_ID,
        "project_path": PROJECT_PATH,
        "base_branch": BASE_BRANCH,
        "work_branch": work_branch,
        "agent_context": AGENT_CONTEXT,
        "scope_files": list(_task_scope(text)),
        "source": {
            "kind": "github_issue",
            "repository": SOURCE_REPOSITORY,
            "issue": issue,
        },
    }

def _select_approved_commit_task(state_root: Path) -> Mapping[str, object] | None:
    approved_root = state_root / "queue/approved"
    if not approved_root.is_dir() or approved_root.is_symlink():
        return None
    candidates: list[Mapping[str, object]] = []
    for path in sorted(approved_root.glob("*.md")):
        try:
            task = _read_approved_task(path, approved_root)
        except MetadataError as exc:
            raise CommitBlocked(str(exc)) from exc
        if task is not None:
            candidates.append(task)
            if len(candidates) > MAX_CANDIDATES:
                raise CommitBlocked("ambiguous_approved_task")
    if not candidates:
        return None
    if len(candidates) != 1:
        raise CommitBlocked("ambiguous_approved_task")
    return candidates[0]

def _read_stage_log(path: Path, logs_root: Path) -> str:
    if path.is_symlink():
        raise CommitBlocked("stage_evidence_symlink_rejected")
    try:
        resolved_root = logs_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if not resolved.is_file() or resolved.stat().st_size > MAX_METADATA_BYTES:
            raise CommitBlocked("stage_evidence_unavailable")
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise CommitBlocked("stage_evidence_unavailable") from exc

def _latest_stage_log(logs_root: Path, task_id: str, stage: str) -> str:
    if not logs_root.is_dir() or logs_root.is_symlink():
        raise CommitBlocked(f"stage_{stage.lower()}_evidence_missing")
    paths = [
        path for path in logs_root.glob(f"{task_id}-{stage}-*.log")
        if path.is_file() and not path.is_symlink()
    ]
    if not paths:
        raise CommitBlocked(f"stage_{stage.lower()}_evidence_missing")
    try:
        latest = max(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))
    except OSError as exc:
        raise CommitBlocked("stage_evidence_unavailable") from exc
    return _read_stage_log(latest, logs_root)

def _verify_stage_evidence(state_root: Path, task_id: str) -> None:
    logs_root = state_root / "logs/orchestrator"
    stage_01b = _latest_stage_log(logs_root, task_id, "01B")
    if not stage_01b.startswith(STAGE_01B_PASS_PREFIXES):
        raise CommitBlocked("stage_01b_not_passed")
    stage_01c = _latest_stage_log(logs_root, task_id, "01C")
    if not stage_01c.startswith(STAGE_01C_PASS_PREFIX):
        raise CommitBlocked("stage_01c_not_passed")

def _repository_base_sha(project: Path) -> str:
    if project.resolve(strict=True) != project or not (project / ".git").is_dir():
        raise CommitBlocked("project_identity_mismatch")
    base_sha = _git(project, "rev-parse", BASE_BRANCH)
    if _git(project, "rev-parse", "origin/main") != base_sha:
        raise CommitBlocked("base_not_reconciled_with_origin_main")
    if SHA_RE.fullmatch(base_sha) is None:
        raise CommitBlocked("missing_base_sha")
    return base_sha

def run_once(root: Path, state_root: Path) -> PublicationDecision:
    task_id: object = None
    try:
        profile = _load_profile(root)
        task = _select_approved_commit_task(state_root)
        if task is None:
            return _decision("OWNER_ACTION_REQUIRED", "approved_task_not_found")
        task_id = task.get("task_id")
        checked_task_id, work_branch, scope_files = _validate_task(task)
        _validate_profile(profile, scope_files)
        _verify_stage_evidence(state_root, checked_task_id)
        project = Path(PROJECT_PATH)
        authorization = CommitAuthorization(
            checked_task_id, work_branch, _repository_base_sha(project), scope_files
        )
        commit_sha = commit_approved_change(project, authorization)
        return _decision(
            "COMMITTED", "local_commit_created_or_resumed",
            authorization.task_id, commit_sha=commit_sha
        )
    except MetadataError as exc:
        return _decision("OWNER_ACTION_REQUIRED", str(exc), task_id)
    except (CommitBlocked, OSError, subprocess.SubprocessError) as exc:
        return _decision("BLOCKED", str(exc), task_id)

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the commit-only AI PROF approved-task gate"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--once", action="store_true", required=True)
    return parser

def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    decision = run_once(args.root, args.state_root)
    print(json.dumps(asdict(decision), sort_keys=True, separators=(",", ":")))
    return 2 if decision.decision == "BLOCKED" else 0

if __name__ == "__main__":
    raise SystemExit(main())
