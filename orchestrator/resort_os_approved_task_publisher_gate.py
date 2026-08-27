#!/usr/bin/env python3
"""Fail-closed trusted publisher for Stage 01C-approved Resort OS tasks.

This adapter reuses the reviewed approved-task publisher machinery but pins it
to exactly one local project/repository pair:

    /home/agent/projects/resort-os -> stvelikiy-star/resort-os -> main

Authority is intentionally narrow: after Stage 01B and independent Stage 01C
PASS, commit only the approved scoped diff, push only the exact GitHub-task
feature branch, open one PR to main, report it, and return the local checkout to
clean main. It never merges, deploys, touches databases, reads secrets, changes
workflow files outside task scope, or executes task prose as shell input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import approved_task_publisher as publisher

RESORT_OS_PROJECT = Path("/home/agent/projects/resort-os")
RESORT_OS_REPOSITORY = "stvelikiy-star/resort-os"
RESORT_OS_BASE_BRANCH = "main"
OWNER = "stvelikiy-star"
_ALLOWED_ORIGIN_URLS = {
    "git@github.com:stvelikiy-star/resort-os.git",
    "https://github.com/stvelikiy-star/resort-os.git",
    "https://github.com/stvelikiy-star/resort-os",
}

_ORIGINAL_COMMIT = publisher.commit_approved_change
_ORIGINAL_PROCESS_TASK = publisher.process_task


def configure_resort_os_profile() -> None:
    """Pin the reused publisher globals to exactly Resort OS."""
    publisher.KOL_PROJECT = RESORT_OS_PROJECT
    publisher.KOL_REPOSITORY = RESORT_OS_REPOSITORY
    publisher.KOL_BASE_BRANCH = RESORT_OS_BASE_BRANCH
    publisher.OWNER = OWNER


def _validate_publish_target(project: Path) -> None:
    resolved = project.resolve(strict=True)
    if resolved != RESORT_OS_PROJECT:
        raise publisher.PublisherError(
            "Resort OS project path resolves outside the fixed target"
        )

    fetch_url = publisher.git_text(project, "remote", "get-url", "origin")
    push_url = publisher.git_text(project, "remote", "get-url", "--push", "origin")
    if fetch_url not in _ALLOWED_ORIGIN_URLS or push_url not in _ALLOWED_ORIGIN_URLS:
        raise publisher.PublisherError(
            "Resort OS origin does not match the fixed private repository"
        )

    result = publisher.run(["gh", "api", f"repos/{RESORT_OS_REPOSITORY}"])
    try:
        repo = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise publisher.PublisherError(
            "GitHub repository identity response is invalid"
        ) from exc

    owner = repo.get("owner") if isinstance(repo, dict) else None
    if not (
        isinstance(repo, dict)
        and repo.get("full_name") == RESORT_OS_REPOSITORY
        and repo.get("private") is True
        and repo.get("default_branch") == RESORT_OS_BASE_BRANCH
        and isinstance(owner, dict)
        and owner.get("login") == OWNER
    ):
        raise publisher.PublisherError(
            "Resort OS GitHub publish target identity/privacy check failed"
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
                "approved Resort OS task branch mismatch before commit"
            )
        if publisher.git_text(project, "rev-parse", "HEAD") != publisher.git_text(
            project, "rev-parse", RESORT_OS_BASE_BRANCH
        ):
            raise publisher.PublisherError(
                "fresh approved Resort OS branch is not based exactly on local main"
            )
        return _ORIGINAL_COMMIT(project, task_id, work_branch, scopes)

    if publisher.git_text(project, "branch", "--show-current") != work_branch:
        raise publisher.PublisherError(
            "approved Resort OS publisher retry branch mismatch"
        )

    head = publisher.git_text(project, "rev-parse", "HEAD")
    parent = publisher.git_text(project, "rev-parse", "HEAD^")
    base = publisher.git_text(project, "rev-parse", RESORT_OS_BASE_BRANCH)
    subject = publisher.git_text(project, "log", "-1", "--format=%s", head)
    expected_subject = f"ai-prof: approved task {task_id}"
    if parent != base or subject != expected_subject:
        raise publisher.PublisherError(
            "clean Resort OS work branch is not an exact resumable approved commit"
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
            "resumable approved Resort OS commit has no changes"
        )
    return head


def _process_task_with_target_gate(paths, task) -> int:
    project = RESORT_OS_PROJECT.resolve(strict=True)
    if project != RESORT_OS_PROJECT:
        raise publisher.PublisherError(
            "Resort OS project path resolves outside the fixed target"
        )
    _validate_publish_target(project)
    return _ORIGINAL_PROCESS_TASK(paths, task)


def _process_one_resort_os(paths) -> int:
    """Consume only approved Resort OS GitHub-task publication items."""
    supported: list[Path] = []
    for task in sorted(paths.approved.glob("*.md")):
        try:
            text = task.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            continue
        if publisher.field(text, "Project-Path") != str(RESORT_OS_PROJECT):
            continue
        if not publisher.WORK_BRANCH_RE.fullmatch(
            publisher.field(text, "Work-Branch")
        ):
            continue
        supported.append(task)

    if supported:
        task = supported[0]
        try:
            return publisher.process_task(paths, task)
        except Exception as exc:
            task_id = publisher.field(
                task.read_text(encoding="utf-8", errors="replace"),
                "Task-ID",
            ) or task.stem
            publisher.write_log(
                paths,
                task_id,
                "RESORT_OS_APPROVED_TASK_PUBLISHER_BLOCKED\n"
                f"{type(exc).__name__}: {publisher.redact(exc)}",
            )
            print(
                f"RESORT_OS_APPROVED_TASK_PUBLISHER_BLOCKED: {publisher.redact(exc)}",
                file=sys.stderr,
            )
            return 1

    print(
        "RESORT_OS_APPROVED_TASK_PUBLISHER_IDLE_MAIN_SYNCED"
        if publisher.sync_clean_main(RESORT_OS_PROJECT)
        else "RESORT_OS_APPROVED_TASK_PUBLISHER_IDLE"
    )
    return 0


def _sample() -> str:
    return "\n".join(
        [
            "Task-ID: RESORT_OS_20260827T000000Z_ABCDEF",
            "Project-Path: /home/agent/projects/resort-os",
            "Base-Branch: main",
            "Work-Branch: feature/chatgpt-issue-140",
            "Goal: smoke",
            "Instructions: Source: authorized private GitHub task issue #140.",
            "Scope-Files: apps/web/a.ts, knowledge/04_CURRENT_STATE.md",
        ]
    )


def run_self_test() -> int:
    configure_resort_os_profile()
    task_id, branch, issue, scopes = publisher.validate_supported_task(_sample())
    assert task_id.startswith("RESORT_OS_")
    assert branch == "feature/chatgpt-issue-140"
    assert issue == 140
    assert scopes == ["apps/web/a.ts", "knowledge/04_CURRENT_STATE.md"]

    for bad_branch in (
        "feature/arbitrary",
        "fix/chatgpt-issue-140",
        "integration/resort-os",
        "main",
    ):
        try:
            publisher.validate_supported_task(
                _sample().replace("feature/chatgpt-issue-140", bad_branch)
            )
        except publisher.PublisherError:
            pass
        else:
            raise AssertionError(f"unsafe Resort OS source branch accepted: {bad_branch}")

    assert publisher.KOL_PROJECT == RESORT_OS_PROJECT
    assert publisher.KOL_REPOSITORY == RESORT_OS_REPOSITORY
    assert publisher.KOL_BASE_BRANCH == "main"
    print("RESORT_OS_APPROVED_TASK_PUBLISHER_SELF_TEST_PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", action="store_true")
    known, _ = parser.parse_known_args()
    if known.self_test:
        return run_self_test()

    configure_resort_os_profile()
    publisher.commit_approved_change = _commit_or_resume
    publisher.process_task = _process_task_with_target_gate
    publisher.process_one = _process_one_resort_os
    result = publisher.main()
    # control_loop treats child rc=1 as a normal task result. Publishing is a
    # repository-state gate, so any publisher failure halts the cycle.
    return 0 if result == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
