#!/usr/bin/env python3
"""Fail-closed control-loop adapter for the approved task publisher.

Adds target-identity validation and bounded retry support for the one safe
partial state where the approved commit was created locally but a later
network/PR step failed.
"""
from __future__ import annotations

import json

import approved_task_publisher as publisher

_ORIGINAL_COMMIT = publisher.commit_approved_change
_ORIGINAL_PROCESS_TASK = publisher.process_task
_ALLOWED_ORIGIN_URLS = {
    "git@github.com:stvelikiy-star/kol-travel-platform.git",
    "https://github.com/stvelikiy-star/kol-travel-platform.git",
    "https://github.com/stvelikiy-star/kol-travel-platform",
}


def _validate_publish_target(project) -> None:
    fetch_url = publisher.git_text(project, "remote", "get-url", "origin")
    push_url = publisher.git_text(project, "remote", "get-url", "--push", "origin")
    if fetch_url not in _ALLOWED_ORIGIN_URLS or push_url not in _ALLOWED_ORIGIN_URLS:
        raise publisher.PublisherError("KÖL origin does not match the fixed private repository")

    result = publisher.run(["gh", "api", f"repos/{publisher.KOL_REPOSITORY}"])
    try:
        repo = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise publisher.PublisherError("GitHub repository identity response is invalid") from exc
    owner = repo.get("owner") if isinstance(repo, dict) else None
    if not (
        isinstance(repo, dict)
        and repo.get("full_name") == publisher.KOL_REPOSITORY
        and repo.get("private") is True
        and repo.get("default_branch") == publisher.KOL_BASE_BRANCH
        and isinstance(owner, dict)
        and owner.get("login") == publisher.OWNER
    ):
        raise publisher.PublisherError("GitHub publish target identity/privacy check failed")


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
    project = publisher.KOL_PROJECT.resolve(strict=True)
    if project != publisher.KOL_PROJECT:
        raise publisher.PublisherError("KÖL project path resolves outside the fixed target")
    _validate_publish_target(project)
    return _ORIGINAL_PROCESS_TASK(paths, task)


def main() -> int:
    publisher.commit_approved_change = _commit_or_resume
    publisher.process_task = _process_task_with_target_gate
    result = publisher.main()
    # control_loop treats child rc=1 as a normal task result. Publishing is a
    # repository-state gate, so any publisher failure must halt the cycle.
    return 0 if result == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
