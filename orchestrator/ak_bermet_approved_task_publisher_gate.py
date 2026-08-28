#!/usr/bin/env python3
"""Fail-closed trusted publisher for Stage 01C-approved AK BERMET tasks.

Reuses the reviewed approved-task publisher machinery but pins it, inside this
process only, to exactly one AK BERMET repository. Authority is publication
only: commit the approved scoped diff, push its feature branch, open a PR to
main, report the PR, and return the local checkout to clean main.

Accepted intake identities are deliberately narrow:
- the existing authorized GitHub issue contract; or
- the exact random branch shape produced by the owner-only Telegram bridge.

Other AK BERMET approved items, such as campaign/integration tasks, are left to
their own runner rather than being treated as publisher failures.

It never merges, deploys, touches databases, reads secrets, or executes task
prose as shell input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import approved_task_publisher as publisher

AK_BERMET_PROJECT = Path("/home/agent/projects/ak-bermet")
AK_BERMET_REPOSITORY = "stvelikiy-star/ak-bermet"
AK_BERMET_BASE_BRANCH = "main"
OWNER = "stvelikiy-star"
TELEGRAM_SOURCE_SENTINEL = 0
TELEGRAM_WORK_BRANCH_RE = re.compile(
    r"^feature/telegram-[0-9a-f]{8}-[0-9a-f]{12}$"
)
_ALLOWED_ORIGIN_URLS = {
    "git@github.com:stvelikiy-star/ak-bermet.git",
    "https://github.com/stvelikiy-star/ak-bermet.git",
    "https://github.com/stvelikiy-star/ak-bermet",
}

_ORIGINAL_COMMIT = publisher.commit_approved_change
_ORIGINAL_PROCESS_TASK = publisher.process_task
_ORIGINAL_PARSE_SOURCE = publisher.parse_source_issue
_ORIGINAL_POST_SOURCE_COMMENT = publisher.post_source_comment


def _is_publication_branch(work_branch: str) -> bool:
    return bool(
        publisher.WORK_BRANCH_RE.fullmatch(work_branch)
        or TELEGRAM_WORK_BRANCH_RE.fullmatch(work_branch)
    )


def _parse_ak_bermet_source(task_text: str, work_branch: str) -> int:
    """Accept only GitHub-gateway or owner-Telegram generated branch identity."""
    if publisher.WORK_BRANCH_RE.fullmatch(work_branch):
        return _ORIGINAL_PARSE_SOURCE(task_text, work_branch)
    if TELEGRAM_WORK_BRANCH_RE.fullmatch(work_branch):
        return TELEGRAM_SOURCE_SENTINEL
    raise publisher.PublisherError(
        "approved AK BERMET task has unsupported intake branch identity"
    )


def _post_source_result(
    source: int,
    task_id: str,
    commit_sha: str,
    pr_url: str,
) -> None:
    """GitHub tasks get an issue comment; Telegram tasks use queue/log evidence."""
    if source == TELEGRAM_SOURCE_SENTINEL:
        return
    _ORIGINAL_POST_SOURCE_COMMENT(source, task_id, commit_sha, pr_url)


def configure_ak_bermet_profile() -> None:
    """Pin the reused publisher globals to exactly AK BERMET."""
    publisher.KOL_PROJECT = AK_BERMET_PROJECT
    publisher.KOL_REPOSITORY = AK_BERMET_REPOSITORY
    publisher.KOL_BASE_BRANCH = AK_BERMET_BASE_BRANCH
    publisher.OWNER = OWNER
    publisher.parse_source_issue = _parse_ak_bermet_source
    publisher.post_source_comment = _post_source_result


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
            "AK BERMET origin does not match the fixed repository"
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
        and repo.get("private") is False
        and repo.get("visibility") == "public"
        and repo.get("default_branch") == AK_BERMET_BASE_BRANCH
        and isinstance(owner, dict)
        and owner.get("login") == OWNER
    ):
        raise publisher.PublisherError(
            "AK BERMET GitHub publish target identity/visibility check failed"
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


def _process_one_ak_bermet(paths) -> int:
    """Consume only approved AK BERMET items intended for PR publication."""
    supported: list[Path] = []
    for task in sorted(paths.approved.glob("*.md")):
        try:
            text = task.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            continue
        if publisher.field(text, "Project-Path") != str(AK_BERMET_PROJECT):
            continue
        if not _is_publication_branch(publisher.field(text, "Work-Branch")):
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
                "AK_BERMET_APPROVED_TASK_PUBLISHER_BLOCKED\n"
                f"{type(exc).__name__}: {publisher.redact(exc)}",
            )
            print(
                f"AK_BERMET_APPROVED_TASK_PUBLISHER_BLOCKED: {publisher.redact(exc)}",
                file=sys.stderr,
            )
            return 1

    print(
        "AK_BERMET_APPROVED_TASK_PUBLISHER_IDLE_MAIN_SYNCED"
        if publisher.sync_clean_main(AK_BERMET_PROJECT)
        else "AK_BERMET_APPROVED_TASK_PUBLISHER_IDLE"
    )
    return 0


def _sample(work_branch: str, instructions: str) -> str:
    return "\n".join(
        [
            "Task-ID: AK_BERMET_20260817T000000Z_ABCDEF",
            "Project-Path: /home/agent/projects/ak-bermet",
            "Base-Branch: main",
            f"Work-Branch: {work_branch}",
            "Goal: smoke",
            f"Instructions: {instructions}",
            "Scope-Files: docs/a.md, src/lib/a.ts",
        ]
    )


def run_self_test() -> int:
    configure_ak_bermet_profile()

    github_sample = _sample(
        "feature/chatgpt-issue-90",
        "Source: authorized private GitHub task issue #90.",
    )
    task_id, branch, issue, scopes = publisher.validate_supported_task(github_sample)
    assert task_id.startswith("AK_BERMET_")
    assert branch == "feature/chatgpt-issue-90"
    assert issue == 90
    assert scopes == ["docs/a.md", "src/lib/a.ts"]

    telegram_sample = _sample(
        "feature/telegram-0123abcd-012345abcdef",
        "owner-requested technical change",
    )
    _task_id, branch, source, _scopes = publisher.validate_supported_task(
        telegram_sample
    )
    assert branch == "feature/telegram-0123abcd-012345abcdef"
    assert source == TELEGRAM_SOURCE_SENTINEL

    for bad_branch in (
        "feature/telegram-short",
        "fix/telegram-0123abcd-012345abcdef",
        "feature/telegram-0123ABCD-012345abcdef",
        "feature/arbitrary",
        "integration/ak-bermet-3day",
    ):
        assert not _is_publication_branch(bad_branch)
        try:
            publisher.validate_supported_task(
                _sample(bad_branch, "owner-requested technical change")
            )
        except publisher.PublisherError:
            pass
        else:
            raise AssertionError(f"unsafe source branch accepted: {bad_branch}")

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
    publisher.process_one = _process_one_ak_bermet
    result = publisher.main()
    return 0 if result == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
