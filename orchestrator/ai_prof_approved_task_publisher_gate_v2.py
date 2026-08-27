#!/usr/bin/env python3
"""Night-safe terminal reconciler for AI PROF commit-only self-maintenance.

This adapter preserves the Slice 5A authority boundary: it may create or resume
exactly one already-authorized local commit, but it still cannot push, create a
PR, merge, deploy, access secrets, or execute task prose.

The additional authority is deliberately narrow and terminal-only:
- after exact Stage 01B + Stage 01C PASS evidence and exact commit verification,
  move that same task atomically from queue/approved to queue/completed;
- when the adapter itself owns the checked-out task branch and the tree is clean,
  return the maintenance checkout to maintenance/base;
- when a previously committed task is stale in approved while another task owns
  the checkout, verify the stale task by its immutable branch ref and complete it
  without touching the other task's checkout or working tree.
"""
from __future__ import annotations

import argparse
import json
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

    def __post_init__(self) -> None:
        if self.published:
            raise ValueError("commit-only reconciliation cannot publish")
        if self.decision not in {
            "OWNER_ACTION_REQUIRED", "COMPLETED", "BLOCKED"
        }:
            raise ValueError("unknown terminal publication decision")
        if self.decision == "COMPLETED":
            if not self.committed or not self.complete:
                raise ValueError("COMPLETED requires committed + complete")
            if not isinstance(self.commit_sha, str) or SHA_RE.fullmatch(self.commit_sha) is None:
                raise ValueError("COMPLETED requires exact commit SHA")
        elif self.committed or self.complete or self.commit_sha is not None:
            raise ValueError("non-completed decision cannot carry terminal commit state")


def _decision(
    decision: str,
    reason: str,
    task_id: object = None,
    *,
    commit_sha: str | None = None,
) -> TerminalPublicationDecision:
    safe_task = (
        task_id
        if isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id)
        else None
    )
    completed = decision == "COMPLETED"
    return TerminalPublicationDecision(
        decision=decision,
        reason=reason,
        task_id=safe_task,
        commit_sha=commit_sha if completed else None,
        committed=completed,
        complete=completed,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run night-safe AI PROF commit terminal reconciler"
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
