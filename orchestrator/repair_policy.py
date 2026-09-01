"""Fail-closed repair classification for monitoring incidents."""
from __future__ import annotations

import json
from pathlib import Path

from monitoring_profiles import load_monitoring_profiles
from project_registry import load_projects

ALLOWED_CLASSES = {"GREEN", "YELLOW", "RED"}


class RepairPolicyError(ValueError):
    pass


def load_repair_policies(root: Path) -> dict[str, dict[str, str]]:
    projects = load_projects(root)
    monitoring = load_monitoring_profiles(root, set(projects))
    path = root / "orchestrator" / "repair_policies.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RepairPolicyError(f"invalid repair policy file: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RepairPolicyError("invalid repair policy structure")
    raw = payload.get("projects")
    if not isinstance(raw, dict):
        raise RepairPolicyError("repair policy projects must be an object")

    unknown_projects = sorted(set(raw) - set(projects))
    if unknown_projects:
        raise RepairPolicyError(
            "repair policy references unregistered project(s): " + ", ".join(unknown_projects)
        )

    result: dict[str, dict[str, str]] = {}
    for project_id, mapping in raw.items():
        if not isinstance(mapping, dict):
            raise RepairPolicyError(f"invalid repair policy for {project_id}")
        known_probes = {
            str(item.get("id"))
            for item in monitoring.get(project_id, {}).get("probes", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        normalized: dict[str, str] = {}
        for probe_id, response_class in mapping.items():
            if probe_id not in known_probes:
                raise RepairPolicyError(
                    f"repair policy references unknown probe: {project_id}:{probe_id}"
                )
            if response_class not in ALLOWED_CLASSES:
                raise RepairPolicyError(
                    f"invalid repair class for {project_id}:{probe_id}: {response_class}"
                )
            normalized[probe_id] = response_class
        result[project_id] = normalized
    return result


def classify(root: Path, project_id: str, probe_id: str) -> str:
    """Return explicit policy or RED when no permission has been granted."""
    policies = load_repair_policies(root)
    return policies.get(project_id, {}).get(probe_id, "RED")
