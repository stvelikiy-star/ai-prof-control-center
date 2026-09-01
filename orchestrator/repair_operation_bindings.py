"""Fail-closed bindings from Repair Team diagnoses to immutable operation profiles.

Presence of a binding is privileged authority. The registry starts empty.
A binding is valid only when it references a registered project/probe, a
verified GREEN runbook with a compatible action, and an existing immutable
OperationProfile whose kind is explicitly compatible with that diagnosis.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from monitoring_profiles import load_monitoring_profiles
from operation_profiles import resolve_profile
from project_registry import load_projects, project_enabled
from runbook_registry import load_runbooks

BINDING_VERSION = 1
BINDING_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{5,79}$")
ACTION_TO_RUNBOOK_ACTION = {
    "SERVICE_RESTART": "restart_service",
    "CONFIG_REPAIR": "restore_known_config",
}
ACTION_TO_PROFILE_KIND = {
    "SERVICE_RESTART": "service-restart",
    "CONFIG_REPAIR": "config-repair",
}


class OperationBindingError(ValueError):
    pass


def load_operation_bindings(root: Path) -> dict[str, dict]:
    projects = load_projects(root)
    profiles = load_monitoring_profiles(root, set(projects))
    runbooks = load_runbooks(root)
    path = root / "orchestrator" / "repair_operation_bindings.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OperationBindingError(f"invalid repair operation binding file: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != BINDING_VERSION:
        raise OperationBindingError("invalid repair operation binding structure")
    raw = payload.get("bindings")
    if not isinstance(raw, list):
        raise OperationBindingError("repair operation bindings must be a list")

    required = {
        "binding_id",
        "project_id",
        "probe_id",
        "suggested_action",
        "operation_profile",
        "operation_kind",
        "required_runbook_id",
        "task_scope",
    }
    result: dict[str, dict] = {}
    seen_routes: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != required:
            raise OperationBindingError("invalid repair operation binding entry")
        binding_id = item.get("binding_id")
        project_id = item.get("project_id")
        probe_id = item.get("probe_id")
        action = item.get("suggested_action")
        profile_key = item.get("operation_profile")
        expected_kind = item.get("operation_kind")
        runbook_id = item.get("required_runbook_id")
        task_scope = item.get("task_scope")
        if not isinstance(binding_id, str) or not BINDING_ID_RE.fullmatch(binding_id):
            raise OperationBindingError("invalid binding_id")
        if binding_id in result:
            raise OperationBindingError(f"duplicate binding_id: {binding_id}")
        if project_id not in projects or not project_enabled(projects[project_id]):
            raise OperationBindingError(f"binding references unavailable project: {project_id}")
        known_probes = {
            probe.get("id")
            for probe in profiles.get(project_id, {}).get("probes", [])
            if isinstance(probe, dict)
        }
        if probe_id not in known_probes:
            raise OperationBindingError(f"binding references unknown probe: {project_id}:{probe_id}")
        if action not in ACTION_TO_RUNBOOK_ACTION:
            raise OperationBindingError(f"unsupported privileged repair action: {action}")
        route = (project_id, probe_id, action)
        if route in seen_routes:
            raise OperationBindingError(f"duplicate privileged repair route: {route}")
        seen_routes.add(route)
        if not isinstance(profile_key, str) or not profile_key:
            raise OperationBindingError(f"operation profile missing: {binding_id}")
        try:
            profile = resolve_profile(profile_key)
        except ValueError as exc:
            raise OperationBindingError(str(exc)) from exc
        required_kind = ACTION_TO_PROFILE_KIND[action]
        if expected_kind != required_kind or profile.kind != required_kind:
            raise OperationBindingError(
                f"operation profile kind is not compatible with {action}: {binding_id}"
            )
        if str(profile.repository) != str(projects[project_id].get("path")):
            raise OperationBindingError(f"operation profile repository mismatch: {binding_id}")
        runbook = runbooks.get(runbook_id)
        if runbook is None:
            raise OperationBindingError(f"required runbook is unavailable: {binding_id}")
        if (
            runbook.get("project_id") != project_id
            or runbook.get("probe_id") != probe_id
            or runbook.get("status") != "verified"
            or runbook.get("response_class") != "GREEN"
            or runbook.get("allowed_action") != ACTION_TO_RUNBOOK_ACTION[action]
            or runbook.get("rollback_verified") is not True
            or not runbook.get("fault_injection_evidence")
        ):
            raise OperationBindingError(f"required runbook is not GREEN-compatible: {binding_id}")
        if not isinstance(task_scope, list) or not task_scope or not all(
            isinstance(entry, str) and entry and entry == entry.strip() for entry in task_scope
        ):
            raise OperationBindingError(f"invalid fixed task scope: {binding_id}")
        normalized = dict(item)
        result[binding_id] = normalized
    return result


def binding_for(
    root: Path,
    project_id: str,
    probe_id: str,
    suggested_action: str,
    eligible_runbook_ids: list[str],
) -> dict | None:
    matches = [
        item
        for item in load_operation_bindings(root).values()
        if item["project_id"] == project_id
        and item["probe_id"] == probe_id
        and item["suggested_action"] == suggested_action
        and item["required_runbook_id"] in eligible_runbook_ids
    ]
    if len(matches) > 1:
        raise OperationBindingError("multiple privileged operation bindings matched")
    return matches[0] if matches else None
