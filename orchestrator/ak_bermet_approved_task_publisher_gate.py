#!/usr/bin/env python3
"""Fail-closed trusted publisher for Stage 01C-approved AK BERMET tasks.

Reuses the reviewed approved-task publisher machinery but pins it, inside this
process only, to exactly one AK BERMET repository. Authority is publication
only: commit the approved scoped diff, push its feature/fix branch, open a PR
to main, report the PR, and return the local checkout to clean main.

It never merges, deploys, touches databases, reads secrets, or executes task
prose as shell input.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import approved_task_publisher as publisher

AK_BERMET_PROJECT = Path("/home/agent/projects/ak-bermet")
AK_BERMET_REPOSITORY = "stvelikiy-star/ak-bermet"
AK_BERMET_BASE_BRANCH = "main"
OWNER = "stvelikiy-star"
_ALLOWED_ORIGIN_URLS = {
    "git@github.com:stvelikiy-star/ak-bermet.git",
    "https://github.com/stvelikiy-star/ak-bermet.git",
    "https://github.com/stvelikiy-star/ak-bermet",
}

_ORIGINAL_COMMIT = publisher.commit_approved_change
_ORIGINAL_PROCESS_TASK = publisher.process_task


def configure_ak_bermet_profile() -> None:
    """Pin the reused publisher globals to exactly AK BERMET."""
    publisher.KOL_PROJECT = AK_BERMET_PROJECT
    publisher.KOL_REPOSITORY = AK_BERMET_REPOSITORY
    publisher.KOL_BASE_BRANCH = AK_BERMET_BASE_BRANCH
    publisher.OWNER = OWNER


def _validate_publish_target(project: Path) -> None:
    resolved = project.resolve(strict=True)
    if resolved != AK_BERMET_PROJECT:
        raise publisher.PublisherError(
            "AK BERMET project path resolves outside the fixed target"
        )

    fetch_url = publisher.git_text(project, "remote", "get-url", "origin")
    push_url = publisher.git_text(project, "remote", "get-url", "--push", "origin")
    if fetch_url not in _ALLOWED_ORIGIN_URLS or push_url not in _ALLOWED_ORIGIN_URLS:
        raise publisher.PublisherError(
            "AK BERMET origin does not match the fixed private repository"
        )

    result = publisher.run(["gh", "api", f"repos/{AK_BERMET_REPOSITORY}"])
    try:
        repo = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise publisher.PublisherError(
            "GitHub repository identity response is invalid"
        ) from exc

    owner = repo.get("owner") if isinstance(repo, dict) else None
    if not (
        isinstance(repo, dict)
        and repo.get("full_name") == AK_BERMET_REPOSITORY
        and repo.get("private") is True
        and repo.get("default_branch") == AK_BERMET_BASE_BRANCH
        and isinstance(owner, dict)
        and owner.get("login") == OWNER
    ):
        raise publisher.PublisherError(
            "AK BERMET GitHub publish target identity/privacy check failed"
        )


def _commit_or_resume(
    project: Path,
    task_id: str,
    work_branch: str,
    scopes: list[str],
) -> str:
    """Commit fresh PASS output or resume one exact approved local commit."""
    paths = publisher.changed_paths(project)
    if paths:
        if publisher.git_text(project, "branch", "--show-current") != work_branch:
            raise publisher.PublisherError(
                "approved AK BERMET task branch mismatch before commit"
            )
        if publisher.git_text(project, "rev-parse", "HEAD") != publisher.git_text(
            project, "rev-parse", AK_BERMET_BASE_BRANCH
        ):
            raise publisher.PublisherError(
                "fresh approved AK BERMET branch is not based exactly on local main"
            )
        return _ORIGINAL_COMMIT(project, task_id, work_branch, scopes)

    if publisher.git_text(project, "branch", "--show-current") != work_branch:
        raise publisher.PublisherError(
            "approved AK BERMET publisher retry branch mismatch"
        )

    head = publisher.git_text(project, "rev-parse", "HEAD")
    parent = publisher.git_text(project, "rev-parse", "HEAD^")
    base = publisher.git_text(project, "rev-parse", AK_BERMET_BASE_BRANCH)
    subject = publisher.git_text(project, "log", "-1", "--format=%s", head)
    expected_subject = f"ai-prof: approved task {task_id}"
    if parent != base or subject != expected_subject:
        raise publisher.PublisherError(
            "clean AK BERMET work branch is not an exact resumable approved commit"
        )

    commit_paths = publisher.nul_paths(
        publisher.run(
            [
                "git",
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                head,
            ],
            cwd=project,
        ).stdout
    )
    publisher.validate_changes_in_scope(project, commit_paths, scopes)
    if not commit_paths:
        raise publisher.PublisherError(
            "resumable approved AK BERMET commit has no changes"
        )
    return head


def _process_task_with_target_gate(paths, task) -> int:
    project = AK_BERMET_PROJECT.resolve(strict=True)
    if project != AK_BERMET_PROJECT:
        raise publisher.PublisherError(
            "AK BERMET project path resolves outside the fixed target"
        )
    _validate_publish_target(project)
    return _ORIGINAL_PROCESS_TASK(paths, task)


def run_self_test() -> int:
    configure_ak_bermet_profile()
    sample = "\n".join(
        [
            "Task-ID: AK_BERMET_20260817T000000Z_ABCDEF",
            "Project-Path: /home/agent/projects/ak-bermet",
            "Base-Branch: main",
            "Work-Branch: feature/chatgpt-issue-90",
            "Goal: smoke",
            "Instructions: Source: authorized private GitHub task issue #90.",
            "Scope-Files: docs/a.md, src/lib/a.ts",
        ]
    )
    task_id, branch, issue, scopes = publisher.validate_supported_task(sample)
    assert task_id.startswith("AK_BERMET_")
    assert branch == "feature/chatgpt-issue-90"
    assert issue == 90
    assert scopes == ["docs/a.md", "src/lib/a.ts"]
    assert publisher.KOL_PROJECT == AK_BERMET_PROJECT
    assert publisher.KOL_REPOSITORY == AK_BERMET_REPOSITORY
    assert publisher.KOL_BASE_BRANCH == "main"
    print("AK_BERMET_APPROVED_TASK_PUBLISHER_SELF_TEST_PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", action="store_true")
    known, _ = parser.parse_known_args()
    if known.self_test:
        return run_self_test()

    configure_ak_bermet_profile()
    publisher.commit_approved_change = _commit_or_resume
    publisher.process_task = _process_task_with_target_gate
    result = publisher.main()
    # control_loop treats child rc=1 as a normal task result. Publishing is a
    # repository-state gate, so any publisher failure halts the cycle.
    return 0 if result == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
