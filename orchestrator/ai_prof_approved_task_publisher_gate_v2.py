#!/usr/bin/env python3
"""Night-safe AI PROF self-maintenance publication reconciler.

GitHub-Issue V3 tasks retain local-commit-only authority. Exact owner-sourced
Telegram V4 tasks additionally publish a verified Draft PR after successful
01B + 01C audits. Merge and deployment are never authorized here.
A single explicit terminal recovery can publish a previously committed owner
V4 task without creating another implementation or replaying Codex.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

try:  # Package import under unit tests.
    from orchestrator import ai_prof_approved_task_publisher_gate as legacy
    from orchestrator import orchestrator as orch
except ImportError:  # Direct script execution from orchestrator/.
    import ai_prof_approved_task_publisher_gate as legacy  # type: ignore[no-redef]
    import orchestrator as orch  # type: ignore[no-redef]

PROJECT_ID = legacy.PROJECT_ID
PROJECT_PATH = legacy.PROJECT_PATH
BASE_BRANCH = legacy.BASE_BRANCH
TASK_ID_RE = legacy.TASK_ID_RE
SHA_RE = legacy.SHA_RE


@dataclass(frozen=True)
class TerminalPublicationDecision:
    decision: str
    reason: str
    task_id: str | None = None
    project_id: str = PROJECT_ID
    commit_sha: str | None = None
    committed: bool = False
    published: bool = False
    complete: bool = False
    pull_request: str | None = None

    def __post_init__(self) -> None:
        if self.decision not in {"OWNER_ACTION_REQUIRED", "COMPLETED", "PUBLISHED", "BLOCKED"}:
            raise ValueError("unknown terminal publication decision")
        if self.decision in {"COMPLETED", "PUBLISHED"}:
            if (not self.committed or not self.complete
                    or not isinstance(self.commit_sha, str)
                    or SHA_RE.fullmatch(self.commit_sha) is None):
                raise ValueError("terminal decision requires exact committed SHA")
            if self.decision == "PUBLISHED":
                if (not self.published or not isinstance(self.pull_request, str)
                        or legacy.re.fullmatch(
                            r"https://github\.com/stvelikiy-star/ai-prof-control-center/pull/[1-9][0-9]*",
                            self.pull_request,
                        ) is None):
                    raise ValueError("published result requires verified Draft PR")
            elif self.published or self.pull_request is not None:
                raise ValueError("commit-only result may not claim publication")
        elif (self.committed or self.complete or self.published
              or self.commit_sha is not None or self.pull_request is not None):
            raise ValueError("nonterminal decision cannot carry result")


def _decision(
    decision: str,
    reason: str,
    task_id: object = None,
    *,
    commit_sha: str | None = None,
    pull_request: str | None = None,
) -> TerminalPublicationDecision:
    safe_task = (
        task_id
        if isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id)
        else None
    )
    completed = decision in {"COMPLETED", "PUBLISHED"}
    return TerminalPublicationDecision(
        decision=decision,
        reason=reason,
        task_id=safe_task,
        commit_sha=commit_sha if completed else None,
        committed=completed,
        published=decision == "PUBLISHED",
        complete=completed,
        pull_request=pull_request if decision == "PUBLISHED" else None,
    )


def _exact_commit_from_branch(
    project: Path,
    authorization: legacy.CommitAuthorization,
) -> str | None:
    """Verify an already-created task commit without inspecting current worktree.

    This path exists specifically for stale approved tasks. Another task may own
    the checkout and may legitimately have uncommitted scoped work, so only the
    immutable task branch ref, parent, subject, and commit paths are inspected.
    """
    try:
        commit_sha = legacy._git(
            project, "rev-parse", "--verify", authorization.work_branch
        )
    except legacy.CommitBlocked:
        return None
    if commit_sha == authorization.base_sha:
        return None
    if SHA_RE.fullmatch(commit_sha) is None:
        raise legacy.CommitBlocked("invalid_created_commit")
    parent = legacy._git(project, "rev-parse", f"{commit_sha}^")
    subject = legacy._git(project, "log", "-1", "--format=%s", commit_sha)
    if parent != authorization.base_sha:
        raise legacy.CommitBlocked("commit_parent_mismatch")
    if subject != legacy.COMMIT_SUBJECT_PREFIX + authorization.task_id:
        raise legacy.CommitBlocked("commit_subject_mismatch")
    if legacy._commit_paths(project, commit_sha) != authorization.scope_files:
        raise legacy.CommitBlocked("commit_scope_mismatch")
    return commit_sha


def _restore_base_if_owned(
    project: Path,
    authorization: legacy.CommitAuthorization,
) -> None:
    """Return to maintenance/base only when this task owns the checkout."""
    current_branch = legacy._git(project, "branch", "--show-current")
    if current_branch != authorization.work_branch:
        return
    if legacy._changed_paths(project):
        raise legacy.CommitBlocked("working_tree_not_clean_after_commit")
    legacy._run(["git", "switch", BASE_BRANCH], cwd=project)
    if legacy._git(project, "branch", "--show-current") != BASE_BRANCH:
        raise legacy.CommitBlocked("base_restore_branch_mismatch")
    if legacy._git(project, "rev-parse", "HEAD") != authorization.base_sha:
        raise legacy.CommitBlocked("base_restore_sha_mismatch")
    if legacy._changed_paths(project):
        raise legacy.CommitBlocked("base_restore_dirty")


def _complete_queue_task(state_root: Path, task_id: str) -> Path:
    approved = state_root / "queue/approved"
    completed = state_root / "queue/completed"
    source = approved / f"{task_id}.md"
    target = completed / source.name
    if source.is_symlink():
        raise legacy.CommitBlocked("approved_task_symlink_rejected")
    if not source.is_file():
        raise legacy.CommitBlocked("approved_task_unavailable")
    if target.exists() or target.is_symlink():
        raise legacy.CommitBlocked("completed_task_already_exists")
    try:
        return orch.safe_move(source, completed)
    except FileExistsError as exc:
        raise legacy.CommitBlocked("completed_task_already_exists") from exc
    except orch.AtomicMoveUnavailable as exc:
        raise legacy.CommitBlocked("atomic_queue_move_unavailable") from exc
    except OSError as exc:
        raise legacy.CommitBlocked("terminal_queue_move_failed") from exc


def run_once(root: Path, state_root: Path) -> TerminalPublicationDecision:
    task_id: object = None
    try:
        profile = legacy._load_profile(root)
        task = legacy._select_approved_commit_task(state_root)
        if task is None:
            return _decision("OWNER_ACTION_REQUIRED", "approved_task_not_found")

        task_id = task.get("task_id")
        checked_task_id, work_branch, scope_files = legacy._validate_task(task)
        legacy._validate_profile(profile, scope_files)
        legacy._verify_stage_evidence(state_root, checked_task_id)

        project = Path(PROJECT_PATH)
        authorization = legacy.CommitAuthorization(
            checked_task_id,
            work_branch,
            legacy._repository_base_sha(project),
            scope_files,
        )

        current_branch = legacy._git(project, "branch", "--show-current")
        if current_branch == authorization.work_branch:
            commit_sha = legacy.commit_approved_change(project, authorization)
            _restore_base_if_owned(project, authorization)
            reason = "local_commit_created_or_resumed_and_terminalized"
        else:
            commit_sha = _exact_commit_from_branch(project, authorization)
            if commit_sha is None:
                raise legacy.CommitBlocked("work_branch_mismatch")
            reason = "stale_exact_commit_terminalized_without_checkout_mutation"

        if legacy._mapping(task.get("source"), "source").get("kind") == "telegram_owner":
            # Only an owner V4 task with exact approved metadata, exact branch,
            # a verified local commit and both stage PASS logs can reach this.
            pr_url = legacy._publish_verified_owner_draft(project, authorization, commit_sha)
            legacy._complete_owner_telegram_task(
                state_root, authorization.task_id, commit_sha, pr_url,
            )
            _restore_base_if_owned(project, authorization)
            return _decision("PUBLISHED", "verified_owner_draft_pr",
                             authorization.task_id, commit_sha=commit_sha,
                             pull_request=pr_url)

        _complete_queue_task(state_root, authorization.task_id)
        return _decision(
            "COMPLETED",
            reason,
            authorization.task_id,
            commit_sha=commit_sha,
        )
    except legacy.MetadataError as exc:
        return _decision("OWNER_ACTION_REQUIRED", str(exc), task_id)
    except (
        legacy.CommitBlocked,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        return _decision("BLOCKED", str(exc), task_id)



def reconcile_completed_owner_task(root: Path, state_root: Path,
                                   task_id: str) -> TerminalPublicationDecision:
    """Explicit one-task recovery from old V2 commit-only terminalization.

    Never scans all completed tasks and never re-executes task instructions.
    The remote commit must match the immutable audited task branch exactly.
    """
    try:
        if TASK_ID_RE.fullmatch(task_id) is None:
            raise legacy.MetadataError("invalid_task_id")
        completed_root = state_root / "queue/completed"
        record = completed_root / f"{task_id}.md"
        task = legacy._read_approved_task(record, completed_root)
        if task is None or legacy._mapping(task.get("source"), "source").get("kind") != "telegram_owner":
            raise legacy.MetadataError("owner_telegram_v4_only")
        checked_id, branch, scope = legacy._validate_task(task)
        legacy._validate_profile(legacy._load_profile(root), scope)
        legacy._verify_stage_evidence(state_root, checked_id)
        project = Path(PROJECT_PATH)
        auth = legacy.CommitAuthorization(
            checked_id, branch, legacy._repository_base_sha(project), scope,
        )
        commit = _exact_commit_from_branch(project, auth)
        if commit is None:
            raise legacy.CommitBlocked("exact_task_commit_not_found")
        old_text = record.read_text(encoding="utf-8")
        if ("Draft-PR:" in old_text or "Commit-SHA:" in old_text):
            raise legacy.CommitBlocked("completed_record_already_published_or_modified")
        # The remote operation is non-forced, SHA-bound, and Draft-only.
        url = legacy._publish_verified_owner_draft(project, auth, commit)
        new_text = old_text.rstrip("\n") + f"\nCommit-SHA: {commit}\nDraft-PR: {url}\n"
        # Fail closed if the terminal record was changed during network I/O.
        if record.is_symlink() or record.read_text(encoding="utf-8") != old_text:
            raise legacy.CommitBlocked("terminal_record_changed_during_publication")
        fd, temporary = tempfile.mkstemp(prefix=".owner-pr-recovery-", dir=completed_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(new_text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, record)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return _decision("PUBLISHED", "exact_terminal_draft_pr_recovered",
                         task_id, commit_sha=commit, pull_request=url)
    except (legacy.MetadataError, legacy.CommitBlocked, OSError,
            subprocess.SubprocessError) as exc:
        return _decision("BLOCKED", str(exc), task_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run night-safe AI PROF commit terminal reconciler"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--recover-completed-task", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.recover_completed_task:
        decision = reconcile_completed_owner_task(
            args.root, args.state_root, args.recover_completed_task,
        )
    else:
        decision = run_once(args.root, args.state_root)
    print(json.dumps(asdict(decision), sort_keys=True, separators=(",", ":")))
    return 2 if decision.decision == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
