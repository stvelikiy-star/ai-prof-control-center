#!/usr/bin/env python3
"""Fail-closed publication-authority gate for AI PROF self-maintenance.

This module is intentionally a decision surface, not a publisher.  It reads a
bounded, validated approval envelope and the pinned project profile, then
reports ``OWNER_ACTION_REQUIRED``.  It has no repository, queue, GitHub,
runtime, database, deployment, or service mutation capability.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


PROJECT_ID = "ai-prof-control-center"
PROJECT_PATH = "/home/agent/projects/ai-prof-control-center-maintenance"
BASE_BRANCH = "maintenance/base"
AGENT_CONTEXT = "agents/ai-prof-control-center"
SOURCE_REPOSITORY = "stvelikiy-star/ai-prof-control-center"
CAPABILITY_FLAGS = (
    "allow_commits",
    "allow_push",
    "allow_merge",
    "allow_deployment",
)
WORK_BRANCH_RE = re.compile(r"feature/chatgpt-issue-([1-9][0-9]*)\Z")
MAX_METADATA_BYTES = 256 * 1024
MAX_CANDIDATES = 32


class MetadataError(ValueError):
    """A bounded metadata input cannot prove this gate's exact identity."""


@dataclass(frozen=True)
class PublicationDecision:
    """Bounded denial result; it can never represent successful publication."""

    decision: str
    reason: str
    task_id: str | None = None
    project_id: str = PROJECT_ID
    published: bool = False
    complete: bool = False

    def __post_init__(self) -> None:
        if self.decision != "OWNER_ACTION_REQUIRED":
            raise ValueError("AI PROF publication decisions must require the owner")
        if self.published or self.complete:
            raise ValueError("denial cannot represent publication or completion")


def _decision(reason: str, task_id: object = None) -> PublicationDecision:
    safe_task_id = task_id if isinstance(task_id, str) and 0 < len(task_id) <= 128 else None
    return PublicationDecision(
        decision="OWNER_ACTION_REQUIRED",
        reason=reason,
        task_id=safe_task_id,
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MetadataError(f"{name}_malformed")
    return value


def _exact_string(
    metadata: Mapping[str, object], key: str, expected: str, reason: str
) -> None:
    value = metadata.get(key)
    if not isinstance(value, str) or value != expected:
        raise MetadataError(reason)


def _required_string(metadata: Mapping[str, object], key: str, reason: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value or len(value) > 512:
        raise MetadataError(reason)
    return value


def _source_issue(task: Mapping[str, object]) -> int:
    """Return a structured GitHub issue identity without interpreting prose."""
    source_value = task.get("source")
    if not isinstance(source_value, Mapping):
        raise MetadataError("wrong_source_identity")
    source = source_value
    _exact_string(source, "kind", "github_issue", "wrong_source_identity")
    _exact_string(
        source, "repository", SOURCE_REPOSITORY, "wrong_source_identity"
    )
    issue = source.get("issue")
    if type(issue) is not int or issue <= 0:  # bool is deliberately rejected.
        raise MetadataError("wrong_source_identity")
    return issue


def _validate_profile(profile: Mapping[str, object]) -> None:
    _exact_string(profile, "project_id", PROJECT_ID, "wrong_profile_identity")
    _exact_string(profile, "path", PROJECT_PATH, "wrong_profile_identity")
    _exact_string(profile, "base_branch", BASE_BRANCH, "wrong_profile_identity")
    _exact_string(
        profile, "agent_context", AGENT_CONTEXT, "wrong_profile_identity"
    )
    allowed_bases = profile.get("allowed_base_branches")
    if (
        not isinstance(allowed_bases, list)
        or allowed_bases != [BASE_BRANCH]
    ):
        raise MetadataError("wrong_profile_identity")
    for flag in CAPABILITY_FLAGS:
        if profile.get(flag) is not False:
            raise MetadataError("unexpected_capability_state")


def _validate_task(task: Mapping[str, object]) -> tuple[str, str]:
    if task.get("lifecycle_state") != "APPROVED":
        raise MetadataError("task_not_approved")
    task_id = _required_string(task, "task_id", "ambiguous_task_identity")
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
    return task_id, work_branch


def _validate_repository(repository: Mapping[str, object], work_branch: str) -> None:
    """Validate already-collected read-only repository identity metadata."""
    _exact_string(repository, "project_id", PROJECT_ID, "wrong_repository_identity")
    _exact_string(repository, "path", PROJECT_PATH, "wrong_repository_identity")
    _exact_string(repository, "base_branch", BASE_BRANCH, "wrong_branch_identity")
    _exact_string(
        repository, "work_branch", work_branch, "wrong_branch_identity"
    )


def evaluate_publication_authority(
    task: object,
    profile: object,
    repository: object,
) -> PublicationDecision:
    """Evaluate a single approved task and always return a denial-only decision.

    Inputs are validated metadata mappings supplied across the existing trusted
    validator boundary.  No path is opened and no command or callback is
    accepted by this evaluator.
    """
    task_id = task.get("task_id") if isinstance(task, Mapping) else None
    try:
        checked_task = _mapping(task, "task")
        checked_profile = _mapping(profile, "profile")
        checked_repository = _mapping(repository, "repository")
        _validate_profile(checked_profile)
        task_id, work_branch = _validate_task(checked_task)
        _validate_repository(checked_repository, work_branch)
    except MetadataError as exc:
        return _decision(str(exc), task_id)
    return _decision("owner_capabilities_disabled", task_id)


def evaluate_approved_tasks(
    tasks: object,
    profile: object,
    repository: object,
) -> PublicationDecision:
    """Select exactly one approved AI PROF task; ambiguity denies authority."""
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes, bytearray)):
        return _decision("approved_tasks_malformed")
    if len(tasks) > MAX_CANDIDATES:
        return _decision("ambiguous_approved_task")

    eligible: list[Mapping[str, object]] = []
    for candidate in tasks:
        if not isinstance(candidate, Mapping):
            return _decision("approved_tasks_malformed")
        try:
            _validate_task(candidate)
        except MetadataError:
            continue
        eligible.append(candidate)
    if len(eligible) != 1:
        return _decision(
            "approved_task_not_found" if not eligible else "ambiguous_approved_task"
        )
    return evaluate_publication_authority(eligible[0], profile, repository)


def _read_json_object(path: Path, boundary: Path) -> Mapping[str, object]:
    """Read one regular, non-symlinked, bounded JSON object inside boundary."""
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
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
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
        item
        for item in projects
        if isinstance(item, Mapping) and item.get("project_id") == PROJECT_ID
    ]
    if len(matching) != 1:
        raise MetadataError("profile_missing_or_ambiguous")
    return matching[0]


def _load_envelope(state_root: Path) -> Mapping[str, object]:
    """Load the validator-owned, read-only Slice 4 approval envelope."""
    return _read_json_object(
        state_root / "validated/ai-prof-approved-task.json", state_root
    )


def run_once(root: Path, state_root: Path) -> PublicationDecision:
    """Read bounded validator metadata and issue one non-mutating decision."""
    try:
        profile = _load_profile(root)
        envelope = _load_envelope(state_root)
        task = envelope.get("task")
        repository = envelope.get("repository")
        return evaluate_publication_authority(task, profile, repository)
    except (MetadataError, OSError):
        return _decision("policy_or_approval_metadata_unavailable")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the denial-only AI PROF approved-task route"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--once", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    decision = run_once(args.root, args.state_root)
    # Fixed fields and enumerated reasons keep stdout bounded and non-sensitive.
    print(json.dumps(asdict(decision), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
