#!/usr/bin/env python3
"""Fail-closed KÖL publisher gate with V4 source-authority validation.

The underlying publisher remains fixed to one KÖL repository and may only
publish an already Stage-01B/01C-approved feature branch. This gate additionally
requires the approved task to carry the exact V4 publication metadata and
re-fetches the owner-authored source issue before any commit or push.
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath

import approved_task_publisher as publisher
import kol_publication_contract_v4 as kol_v4

_ORIGINAL_COMMIT = publisher.commit_approved_change
_ORIGINAL_PROCESS_TASK = publisher.process_task
_ORIGINAL_PATH_IN_SCOPE = publisher.path_in_scope
_ALLOWED_ORIGIN_URLS = {
    "git@github.com:stvelikiy-star/kol-travel-platform.git",
    "https://github.com/stvelikiy-star/kol-travel-platform.git",
    "https://github.com/stvelikiy-star/kol-travel-platform",
}


def _validate_publish_target(project) -> None:
    fetch_url = publisher.git_text(project, "remote", "get-url", "origin")
    push_url = publisher.git_text(project, "remote", "get-url", "--push", "origin")
    if fetch_url not in _ALLOWED_ORIGIN_URLS or push_url not in _ALLOWED_ORIGIN_URLS:
        raise publisher.PublisherError("KÖL origin does not match the fixed repository")

    result = publisher.run(["gh", "api", f"repos/{publisher.KOL_REPOSITORY}"])
    try:
        repo = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise publisher.PublisherError("GitHub repository identity response is invalid") from exc
    owner = repo.get("owner") if isinstance(repo, dict) else None
    if not (
        isinstance(repo, dict)
        and repo.get("full_name") == publisher.KOL_REPOSITORY
        and repo.get("private") is False
        and repo.get("visibility") == "public"
        and repo.get("default_branch") == publisher.KOL_BASE_BRANCH
        and isinstance(owner, dict)
        and owner.get("login") == publisher.OWNER
    ):
        raise publisher.PublisherError("GitHub publish target identity/visibility check failed")


def _v4_path_in_scope(project, relative: str, scopes: list[str]) -> bool:
    """Support exact files/directories plus intake-style `directory/**` scope."""
    candidate = PurePosixPath(relative)
    for scope in scopes:
        if scope.endswith("/**"):
            prefix = PurePosixPath(scope[:-3])
            if candidate == prefix or prefix in candidate.parents:
                return True
            continue
        scope_path = PurePosixPath(scope)
        if candidate == scope_path:
            return True
        if (project / scope).is_dir() and scope_path in candidate.parents:
            return True
    return False


def _validate_v4_authority(task_text: str) -> None:
    metadata = kol_v4.parse_task_publication_metadata(task_text)
    work_branch = publisher.field(task_text, "Work-Branch")
    source_issue = publisher.parse_source_issue(task_text, work_branch)
    if metadata["source_issue"] != source_issue:
        raise publisher.PublisherError("V4 metadata source issue differs from task source marker")

    result = publisher.run(
        [
            "gh",
            "api",
            "-X",
            "GET",
            f"repos/{publisher.CONTROL_REPOSITORY}/issues/{source_issue}",
        ]
    )
    try:
        issue = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise publisher.PublisherError("KÖL V4 source issue response is invalid") from exc
    if not isinstance(issue, dict):
        raise publisher.PublisherError("KÖL V4 source issue payload is invalid")

    try:
        contract = kol_v4.parse_contract(issue)
    except kol_v4.KolV4Error as exc:
        raise publisher.PublisherError(f"KÖL V4 source authority rejected: {exc}") from exc

    scopes = publisher.parse_scope_files(task_text)
    if contract["scope"] != scopes:
        raise publisher.PublisherError("KÖL V4 source scope differs from approved task scope")
    if contract["title"] != publisher.field(task_text, "Goal"):
        raise publisher.PublisherError("KÖL V4 source title differs from approved task goal")
    if contract["digest"] != metadata["digest"]:
        raise publisher.PublisherError("KÖL V4 source contract digest mismatch")
    if contract["allowed_actions"] != metadata["allowed_actions"]:
        raise publisher.PublisherError("KÖL V4 allowed publication authority mismatch")
    if contract["forbidden_actions"] != metadata["forbidden_actions"]:
        raise publisher.PublisherError("KÖL V4 forbidden publication boundary mismatch")


def _commit_or_resume(project, task_id: str, work_branch: str, scopes: list[str]) -> str:
    """Commit fresh PASS output or resume only one exact approved commit."""
    paths = publisher.changed_paths(project)
    if paths:
        if publisher.git_text(project, "branch", "--show-current") != work_branch:
            raise publisher.PublisherError("approved task branch mismatch before commit")
        if publisher.git_text(project, "rev-parse", "HEAD") != publisher.git_text(
            project, "rev-parse", publisher.KOL_BASE_BRANCH
        ):
            raise publisher.PublisherError("fresh approved work branch is not based exactly on local main")
        return _ORIGINAL_COMMIT(project, task_id, work_branch, scopes)

    if publisher.git_text(project, "branch", "--show-current") != work_branch:
        raise publisher.PublisherError("approved publisher retry branch mismatch")

    head = publisher.git_text(project, "rev-parse", "HEAD")
    parent = publisher.git_text(project, "rev-parse", "HEAD^")
    base = publisher.git_text(project, "rev-parse", publisher.KOL_BASE_BRANCH)
    subject = publisher.git_text(project, "log", "-1", "--format=%s", head)
    expected_subject = f"ai-prof: approved task {task_id}"
    if parent != base or subject != expected_subject:
        raise publisher.PublisherError("clean work branch is not an exact resumable approved commit")

    commit_paths = publisher.nul_paths(
        publisher.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", head],
            cwd=project,
        ).stdout
    )
    publisher.validate_changes_in_scope(project, commit_paths, scopes)
    if not commit_paths:
        raise publisher.PublisherError("resumable approved commit has no changes")
    return head


def _process_task_with_target_gate(paths, task) -> int:
    task_text = task.read_text(encoding="utf-8", errors="strict")
    # V1/V2/V3 KÖL tasks are intentionally non-publishable. Authority must be
    # proven from the exact V4 source issue before target validation or commit.
    _validate_v4_authority(task_text)

    project = publisher.KOL_PROJECT.resolve(strict=True)
    if project != publisher.KOL_PROJECT:
        raise publisher.PublisherError("KÖL project path resolves outside the fixed target")
    _validate_publish_target(project)
    return _ORIGINAL_PROCESS_TASK(paths, task)


def main() -> int:
    publisher.commit_approved_change = _commit_or_resume
    publisher.process_task = _process_task_with_target_gate
    publisher.path_in_scope = _v4_path_in_scope
    result = publisher.main()
    # control_loop treats child rc=1 as a normal task result. Publishing is a
    # repository-state gate, so any publisher failure must halt the cycle.
    return 0 if result == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
