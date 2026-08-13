#!/usr/bin/env python3
"""Fail-closed control-loop adapter for the approved task publisher.

Also provides bounded retry support for the one safe partial state where the
approved commit was created locally but a later network/PR step failed.
"""
from __future__ import annotations

import approved_task_publisher as publisher

_ORIGINAL_COMMIT = publisher.commit_approved_change


def _commit_or_resume(project, task_id: str, work_branch: str, scopes: list[str]) -> str:
    """Resume only an exact, clean, single approved commit on the work branch."""
    paths = publisher.changed_paths(project)
    if paths:
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


def main() -> int:
    publisher.commit_approved_change = _commit_or_resume
    result = publisher.main()
    # control_loop treats child rc=1 as a normal task result. Publishing is a
    # repository-state gate, so any publisher failure must halt the cycle.
    return 0 if result == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
