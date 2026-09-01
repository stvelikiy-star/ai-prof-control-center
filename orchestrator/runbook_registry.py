"""Strict repair-runbook registry.

A GREEN runbook is invalid unless fault-injection and rollback evidence are
explicitly recorded. Merely editing JSON can never silently promote an
untested action to autonomous production authority.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from monitoring_profiles import load_monitoring_profiles
from project_registry import load_projects

RUNBOOK_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{5,79}$")
ALLOWED_STATUS = {"draft", "verified", "retired"}
ALLOWED_CLASSES = {"GREEN", "YELLOW", "RED"}
ALLOWED_ACTIONS = {
    "restart_service",
    "retry_task",
    "restore_known_config",
    "code_patch",
    "no_action",
}


class RunbookError(ValueError):
    pass


def load_runbooks(root: Path) -> dict[str, dict]:
    projects = load_projects(root)
    profiles = load_monitoring_profiles(root, set(projects))
    path = root / "orchestrator" / "repair_runbooks.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunbookError(f"invalid runbook file: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RunbookError("invalid runbook structure")
    raw = payload.get("runbooks")
    if not isinstance(raw, list):
        raise RunbookError("runbooks must be a list")

    result: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise RunbookError("runbook must be an object")
        runbook_id = item.get("runbook_id")
        if not isinstance(runbook_id, str) or not RUNBOOK_ID_RE.fullmatch(runbook_id):
            raise RunbookError("invalid runbook_id")
        if runbook_id in result:
            raise RunbookError(f"duplicate runbook_id: {runbook_id}")
        project_id = item.get("project_id")
        probe_id = item.get("probe_id")
        if project_id not in projects:
            raise RunbookError(f"unregistered runbook project: {project_id}")
        known_probes = {
            probe.get("id")
            for probe in profiles.get(project_id, {}).get("probes", [])
            if isinstance(probe, dict)
        }
        if probe_id not in known_probes:
            raise RunbookError(f"unknown runbook probe: {project_id}:{probe_id}")
        status = item.get("status")
        response_class = item.get("response_class")
        action = item.get("allowed_action")
        tests = item.get("required_tests")
        rollback = item.get("rollback")
        evidence = item.get("fault_injection_evidence")
        rollback_verified = item.get("rollback_verified")
        if status not in ALLOWED_STATUS:
            raise RunbookError(f"invalid runbook status: {runbook_id}")
        if response_class not in ALLOWED_CLASSES:
            raise RunbookError(f"invalid response_class: {runbook_id}")
        if action not in ALLOWED_ACTIONS:
            raise RunbookError(f"invalid allowed_action: {runbook_id}")
        if not isinstance(tests, list) or not tests or not all(isinstance(x, str) and x.strip() for x in tests):
            raise RunbookError(f"required_tests missing: {runbook_id}")
        if not isinstance(rollback, str) or not rollback.strip():
            raise RunbookError(f"rollback missing: {runbook_id}")
        if not isinstance(evidence, list) or not all(isinstance(x, str) and x.strip() for x in evidence):
            raise RunbookError(f"invalid fault_injection_evidence: {runbook_id}")
        if not isinstance(rollback_verified, bool):
            raise RunbookError(f"invalid rollback_verified: {runbook_id}")
        if response_class == "GREEN":
            if status != "verified":
                raise RunbookError(f"GREEN runbook must be verified: {runbook_id}")
            if not evidence:
                raise RunbookError(f"GREEN runbook requires fault injection evidence: {runbook_id}")
            if rollback_verified is not True:
                raise RunbookError(f"GREEN runbook requires verified rollback: {runbook_id}")
        result[runbook_id] = item
    return result


def eligible_green_runbooks(root: Path, project_id: str, probe_id: str) -> list[dict]:
    return [
        item for item in load_runbooks(root).values()
        if item["project_id"] == project_id
        and item["probe_id"] == probe_id
        and item["response_class"] == "GREEN"
        and item["status"] == "verified"
    ]
