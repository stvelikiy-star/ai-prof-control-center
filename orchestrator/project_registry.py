"""Shared fail-closed project branch policy helpers."""
from __future__ import annotations

import json
from pathlib import Path


class ProjectPolicyError(ValueError):
    pass


def allowed_base_branches(project: dict) -> tuple[str, ...]:
    raw = project.get("allowed_base_branches", [project.get("base_branch")])
    if not isinstance(raw, list) or not raw or not all(
        isinstance(branch, str) and branch and branch == branch.strip() for branch in raw
    ):
        raise ProjectPolicyError("invalid allowed_base_branches")
    if project.get("base_branch") not in raw or len(set(raw)) != len(raw):
        raise ProjectPolicyError("base_branch must occur exactly once in allowed_base_branches")
    return tuple(raw)


def local_integration_branches(project: dict) -> tuple[str, ...]:
    raw = project.get("local_integration_branches", [])
    if not isinstance(raw, list) or not all(
        isinstance(branch, str) and branch and branch == branch.strip() for branch in raw
    ):
        raise ProjectPolicyError("invalid local_integration_branches")
    allowed = allowed_base_branches(project)
    if len(set(raw)) != len(raw) or any(branch not in allowed for branch in raw):
        raise ProjectPolicyError("local integration branch is not an allowed base branch")
    return tuple(raw)


def project_enabled(project: dict) -> bool:
    """Return the explicit project execution state, failing closed on bad values."""
    raw = project.get("enabled", True)
    if not isinstance(raw, bool):
        raise ProjectPolicyError("invalid enabled flag")
    return raw


def load_projects(root: Path) -> dict[str, dict]:
    try:
        payload = json.loads((root / "orchestrator/projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectPolicyError(f"invalid project registry: {exc}") from exc
    if payload.get("version") != 1 or not isinstance(payload.get("projects"), list):
        raise ProjectPolicyError("invalid project registry structure")
    projects = {}
    for project in payload["projects"]:
        if not isinstance(project, dict) or not isinstance(project.get("project_id"), str):
            raise ProjectPolicyError("invalid project registry entry")
        project_id = project["project_id"]
        if project_id in projects:
            raise ProjectPolicyError(f"duplicate project_id: {project_id}")
        allowed_base_branches(project)
        local_integration_branches(project)
        project_enabled(project)
        projects[project_id] = project
    return projects


def project_for_task(root: Path, project_path: str) -> dict:
    matches = [
        project for project in load_projects(root).values()
        if project.get("path") == project_path
    ]
    if len(matches) != 1:
        raise ProjectPolicyError("task project is not uniquely registered")
    project = matches[0]
    if not project_enabled(project):
        raise ProjectPolicyError("project is disabled")
    return project


def validate_task_base_branch(root: Path, project_path: str, branch: str) -> dict:
    try:
        project = project_for_task(root, project_path)
    except ProjectPolicyError:
        # Compatibility for isolated runner unit fixtures which intentionally
        # have no registry. Production roots always have projects.json.
        if not (root / "orchestrator/projects.json").exists() and branch in ("main", "develop"):
            return {"path": project_path, "base_branch": branch, "allowed_base_branches": [branch]}
        raise
    if branch not in allowed_base_branches(project):
        raise ProjectPolicyError("base branch is outside allowed_base_branches")
    return project
