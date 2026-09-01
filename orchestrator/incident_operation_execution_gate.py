"""Execution-time authority revalidation for Repair Team privileged operations.

This module adds no privileged authority. It fails closed unless an
`incident-operation` task still matches the current open incident, diagnosis,
GREEN policy, verified runbook, production-ready recovery contract, owner-
managed operation binding and immutable operation profile at execution time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import repair_operations_bridge as bridge
import submit_task
from operation_profiles import resolve_profile
from repair_operation_bindings import load_operation_bindings


class IncidentOperationGateError(ValueError):
    pass


_RESERVED_FIELDS = (
    "Incident-ID",
    "Diagnosis-SHA256",
    "Repair-Response-Class",
    "Repair-Operation-Binding",
    "Repair-Runbook-IDs",
)


def _block(reason: str) -> None:
    raise IncidentOperationGateError(f"BLOCKED_INCIDENT_OPERATION_AUTHORITY: {reason}")


def _split_csv(value: str) -> list[str]:
    if value.strip().lower() in {"none", "-"}:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _current_bindings(root: Path, binding_loader: Callable) -> dict:
    try:
        bindings = binding_loader(root)
    except Exception as exc:
        _block(f"current privileged bindings are invalid: {exc}")
    if not isinstance(bindings, dict):
        _block("current privileged bindings have invalid shape")
    return bindings


def validate_incident_operation_authority(
    root: Path,
    state_root: Path,
    data: dict[str, str],
    *,
    result_reader: Callable = bridge._read_result,
    diagnosis_validator: Callable = bridge._validate_diagnosis,
    binding_loader: Callable = load_operation_bindings,
    profile_resolver: Callable = resolve_profile,
    registry_reader: Callable = submit_task.read_registry,
    fixed_scope_resolver: Callable = bridge._fixed_scope,
) -> None:
    """Revalidate an incident-origin privileged task immediately before execution."""
    origin = data.get("Repair-Origin", "")
    reserved_present = any(data.get(field) for field in _RESERVED_FIELDS)
    if not origin:
        if reserved_present:
            _block("reserved metadata without Repair-Origin")
        if data.get("Execution-Mode") == "operations":
            profile_key = data.get("Operation-Profile", "")
            if profile_key and profile_key != "none":
                bindings = _current_bindings(root, binding_loader)
                if any(
                    isinstance(item, dict) and item.get("operation_profile") == profile_key
                    for item in bindings.values()
                ):
                    _block("bound privileged profile requires incident-operation provenance")
        return
    if origin != "incident-operation":
        _block("unexpected Repair-Origin")
    if data.get("Execution-Mode") != "operations":
        _block("incident operation is not operations mode")

    required = (
        "Task-ID",
        "Project-Path",
        "Base-Branch",
        "Work-Branch",
        "Agent-Context",
        "Operation-Profile",
        "Scope-Files",
        "Incident-ID",
        "Diagnosis-SHA256",
        "Repair-Response-Class",
        "Repair-Operation-Binding",
        "Repair-Runbook-IDs",
    )
    missing = [field for field in required if not data.get(field)]
    if missing:
        _block("missing metadata: " + ", ".join(missing))

    incident_id = data["Incident-ID"]
    if not bridge.diagnosis_runner.INCIDENT_ID_RE.fullmatch(incident_id):
        _block("invalid incident id")

    result_path = state_root / "diagnosis" / "results" / f"{incident_id}.json"
    try:
        payload, current_sha256 = result_reader(result_path)
        diagnosis, eligible_runbooks = diagnosis_validator(root, state_root, payload)
    except Exception as exc:
        _block(f"current diagnosis is unavailable or invalid: {exc}")

    if current_sha256 != data["Diagnosis-SHA256"]:
        _block("diagnosis SHA drift")
    if diagnosis.get("incident_id") != incident_id:
        _block("diagnosis incident mismatch")
    if payload.get("response_class") != "GREEN" or data["Repair-Response-Class"] != "GREEN":
        _block("response class is not current GREEN")

    bindings = _current_bindings(root, binding_loader)
    binding = bindings.get(data["Repair-Operation-Binding"])
    if binding is None:
        _block("binding is no longer available")

    project_id = payload.get("project_id")
    probe_id = payload.get("probe_id")
    action = diagnosis.get("suggested_action")
    if (
        binding.get("project_id") != project_id
        or binding.get("probe_id") != probe_id
        or binding.get("suggested_action") != action
    ):
        _block("binding route drift")
    required_runbook = binding.get("required_runbook_id")
    if required_runbook not in eligible_runbooks:
        _block("binding runbook is no longer eligible")
    if _split_csv(data["Repair-Runbook-IDs"]) != [required_runbook]:
        _block("task runbook metadata drift")

    try:
        profile = profile_resolver(binding["operation_profile"])
    except Exception as exc:
        _block(f"operation profile unavailable: {exc}")
    if data["Operation-Profile"] != binding.get("operation_profile") or profile.key != data["Operation-Profile"]:
        _block("operation profile drift")
    if getattr(profile, "kind", None) != binding.get("operation_kind"):
        _block("operation profile kind drift")

    try:
        projects = registry_reader(root)
    except Exception as exc:
        _block(f"project registry invalid: {exc}")
    project = projects.get(project_id)
    if project is None:
        _block("project is no longer registered")
    if data["Project-Path"] != project.get("path") or str(profile.repository) != project.get("path"):
        _block("project path drift")
    if data["Base-Branch"] != project.get("base_branch"):
        _block("base branch drift")
    if data["Agent-Context"] != project.get("agent_context"):
        _block("agent context drift")

    expected_task_id = bridge._task_id(project_id, incident_id)
    expected_work_branch = bridge._work_branch(incident_id)
    if data["Task-ID"] != expected_task_id:
        _block("task id drift")
    if data["Work-Branch"] != expected_work_branch:
        _block("work branch drift")

    try:
        expected_scope = fixed_scope_resolver(project, binding["task_scope"])
    except Exception as exc:
        _block(f"current fixed scope invalid: {exc}")
    task_scope = _split_csv(data["Scope-Files"])
    if task_scope != expected_scope:
        _block("fixed scope drift")
