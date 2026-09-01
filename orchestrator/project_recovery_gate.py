"""Project-level recovery readiness gate for AI PROF Repair Team.

This module records evidence only; it performs no backup, restore, rollback or
deployment. A project is production recovery-ready only when the reviewed
contract explicitly says so *and* contains checkpoint, rollback, restore-test,
and fault-injection evidence. Unknown or partial recovery state fails closed.
"""
from __future__ import annotations

import json
from pathlib import Path

from project_registry import load_projects, project_enabled

CONTRACT_VERSION = 1
ALLOWED_MODES = {"unverified", "prepare_only", "staged_activation", "verified"}


class RecoveryGateError(ValueError):
    pass


def _repair_capable_projects(projects: dict[str, dict]) -> set[str]:
    result = set()
    for project_id, project in projects.items():
        if not project_enabled(project):
            continue
        checks = project.get("code_required_checks", [])
        if isinstance(checks, list) and checks:
            result.add(project_id)
    return result


def _evidence_list(item: dict, key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry and entry == entry.strip() for entry in value
    ):
        raise RecoveryGateError(f"invalid {key} for {item.get('project_id')}")
    if len(set(value)) != len(value):
        raise RecoveryGateError(f"duplicate {key} for {item.get('project_id')}")
    return list(value)


def load_recovery_contracts(root: Path) -> dict[str, dict]:
    projects = load_projects(root)
    path = root / "orchestrator" / "project_recovery_contracts.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryGateError(f"invalid recovery contract file: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != CONTRACT_VERSION:
        raise RecoveryGateError("invalid recovery contract structure")
    raw = payload.get("projects")
    if not isinstance(raw, list):
        raise RecoveryGateError("recovery contracts projects must be a list")

    required = {
        "project_id",
        "recovery_mode",
        "checkpoint_evidence",
        "rollback_evidence",
        "restore_test_evidence",
        "fault_injection_evidence",
        "production_ready",
    }
    result: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict) or set(item) != required:
            raise RecoveryGateError("invalid recovery contract entry")
        project_id = item.get("project_id")
        if project_id not in projects or not project_enabled(projects[project_id]):
            raise RecoveryGateError(f"recovery contract references unavailable project: {project_id}")
        if project_id in result:
            raise RecoveryGateError(f"duplicate recovery contract: {project_id}")
        mode = item.get("recovery_mode")
        if mode not in ALLOWED_MODES:
            raise RecoveryGateError(f"invalid recovery mode: {project_id}")
        checkpoint = _evidence_list(item, "checkpoint_evidence")
        rollback = _evidence_list(item, "rollback_evidence")
        restore = _evidence_list(item, "restore_test_evidence")
        fault = _evidence_list(item, "fault_injection_evidence")
        ready = item.get("production_ready")
        if not isinstance(ready, bool):
            raise RecoveryGateError(f"invalid production_ready: {project_id}")
        if ready:
            if mode != "verified":
                raise RecoveryGateError(
                    f"production-ready recovery contract must use verified mode: {project_id}"
                )
            if not checkpoint or not rollback or not restore or not fault:
                raise RecoveryGateError(
                    f"production-ready recovery contract lacks evidence: {project_id}"
                )
        normalized = dict(item)
        result[project_id] = normalized

    expected = _repair_capable_projects(projects)
    missing = sorted(expected - set(result))
    if missing:
        raise RecoveryGateError(
            "missing recovery contract for repair-capable project(s): " + ", ".join(missing)
        )
    extra = sorted(set(result) - expected)
    if extra:
        raise RecoveryGateError(
            "recovery contract exists for project without required code checks: " + ", ".join(extra)
        )
    return result


def recovery_readiness(root: Path, project_id: str) -> tuple[bool, list[str]]:
    contracts = load_recovery_contracts(root)
    try:
        item = contracts[project_id]
    except KeyError as exc:
        raise RecoveryGateError(f"no recovery contract for project: {project_id}") from exc
    blockers: list[str] = []
    if item["recovery_mode"] != "verified":
        blockers.append("RECOVERY_MODE_NOT_VERIFIED")
    if not item["checkpoint_evidence"]:
        blockers.append("CHECKPOINT_EVIDENCE_MISSING")
    if not item["rollback_evidence"]:
        blockers.append("ROLLBACK_EVIDENCE_MISSING")
    if not item["restore_test_evidence"]:
        blockers.append("RESTORE_TEST_EVIDENCE_MISSING")
    if not item["fault_injection_evidence"]:
        blockers.append("FAULT_INJECTION_EVIDENCE_MISSING")
    if item["production_ready"] is not True:
        blockers.append("PRODUCTION_RECOVERY_NOT_APPROVED")
    return not blockers, blockers


def require_recovery_ready(root: Path, project_id: str) -> dict:
    ready, blockers = recovery_readiness(root, project_id)
    if not ready:
        raise RecoveryGateError(
            f"project recovery is not production-ready: {project_id}: {','.join(blockers)}"
        )
    return load_recovery_contracts(root)[project_id]
