"""Strict monitoring-profile loader bound to the registered project inventory."""
from __future__ import annotations

import json
from pathlib import Path


class MonitoringProfileError(ValueError):
    pass


def load_monitoring_profiles(root: Path, registered_project_ids: set[str]) -> dict[str, dict]:
    path = root / "orchestrator" / "monitoring_profiles.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise MonitoringProfileError(f"invalid monitoring profile file: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise MonitoringProfileError("invalid monitoring profile structure")
    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, dict):
        raise MonitoringProfileError("monitoring projects must be an object")

    unknown = sorted(set(raw_projects) - registered_project_ids)
    if unknown:
        raise MonitoringProfileError(
            "monitoring profile references unregistered project(s): " + ", ".join(unknown)
        )

    profiles: dict[str, dict] = {}
    for project_id, profile in raw_projects.items():
        if not isinstance(project_id, str) or not project_id or project_id != project_id.strip():
            raise MonitoringProfileError("invalid project id in monitoring profiles")
        if not isinstance(profile, dict):
            raise MonitoringProfileError(f"invalid monitoring profile for {project_id}")
        profiles[project_id] = profile
    return profiles
