"""Project-level recovery readiness gate for AI PROF Repair Team.

This module records and validates recovery evidence only; it performs no backup,
restore, rollback or deployment. A project is production recovery-ready only
when the reviewed contract explicitly says so, every evidence reference resolves
to a real source marker outside autonomous self-maintenance scope, and recovery
has been verified at staging or production level. Unit/shadow evidence can
improve confidence but never grants production authority by itself. Unknown,
stale or partial recovery state fails closed.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from project_registry import load_projects, project_enabled

CONTRACT_VERSION = 1
ALLOWED_MODES = {"unverified", "prepare_only", "staged_activation", "verified"}
ALLOWED_VERIFICATION_LEVELS = {"none", "unit", "shadow", "staging", "production"}
PRODUCTION_CAPABLE_LEVELS = {"staging", "production"}
MAX_EVIDENCE_FILE_BYTES = 2 * 1024 * 1024


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


def _path_has_symlink(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _scope_matches(source: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return source == prefix or source.startswith(prefix + "/")
    return fnmatch.fnmatchcase(source, pattern)


def _self_maintenance_writable(root: Path, source: str) -> bool:
    projects = load_projects(root)
    control = projects.get("ai-prof-control-center")
    if not control or not project_enabled(control):
        return False
    allowed = control.get("allowed_scope", [])
    if not isinstance(allowed, list):
        raise RecoveryGateError("invalid AI PROF self-maintenance allowed_scope")
    return any(
        isinstance(pattern, str) and pattern and _scope_matches(source, pattern)
        for pattern in allowed
    )


def _verify_evidence_reference(root: Path, project_id: str, key: str, entry: str) -> str:
    source, separator, marker = entry.partition(":")
    if (
        not separator
        or not source
        or not marker
        or source != source.strip()
        or marker != marker.strip()
        or len(marker) < 4
        or "\x00" in marker
        or "\n" in marker
        or "\r" in marker
        or "\\" in source
    ):
        raise RecoveryGateError(f"invalid {key} evidence reference for {project_id}")
    relative = Path(source)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RecoveryGateError(f"unsafe {key} evidence path for {project_id}")
    if _self_maintenance_writable(root, source):
        raise RecoveryGateError(
            f"self-maintenance-writable {key} evidence rejected for {project_id}: {source}"
        )

    root_resolved = root.resolve(strict=True)
    lexical = root_resolved / relative
    if _path_has_symlink(root_resolved, relative):
        raise RecoveryGateError(f"symlink {key} evidence rejected for {project_id}: {source}")
    try:
        candidate = lexical.resolve(strict=True)
        candidate.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise RecoveryGateError(
            f"missing or escaped {key} evidence for {project_id}: {source}"
        ) from exc
    try:
        stat_result = candidate.stat()
    except OSError as exc:
        raise RecoveryGateError(f"cannot stat {key} evidence for {project_id}: {source}") from exc
    if not candidate.is_file() or stat_result.st_size > MAX_EVIDENCE_FILE_BYTES:
        raise RecoveryGateError(f"invalid {key} evidence file for {project_id}: {source}")
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RecoveryGateError(f"unreadable {key} evidence for {project_id}: {source}") from exc
    if marker not in text:
        raise RecoveryGateError(
            f"stale {key} evidence marker for {project_id}: {source}:{marker}"
        )
    return f"{source}:{marker}"


def _evidence_list(root: Path, item: dict, key: str) -> list[str]:
    value = item.get(key)
    project_id = item.get("project_id")
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry and entry == entry.strip() for entry in value
    ):
        raise RecoveryGateError(f"invalid {key} for {project_id}")
    if len(set(value)) != len(value):
        raise RecoveryGateError(f"duplicate {key} for {project_id}")
    return [_verify_evidence_reference(root, str(project_id), key, entry) for entry in value]


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
        "verification_level",
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
        verification_level = item.get("verification_level")
        if verification_level not in ALLOWED_VERIFICATION_LEVELS:
            raise RecoveryGateError(f"invalid recovery verification level: {project_id}")
        checkpoint = _evidence_list(root, item, "checkpoint_evidence")
        rollback = _evidence_list(root, item, "rollback_evidence")
        restore = _evidence_list(root, item, "restore_test_evidence")
        fault = _evidence_list(root, item, "fault_injection_evidence")
        ready = item.get("production_ready")
        if not isinstance(ready, bool):
            raise RecoveryGateError(f"invalid production_ready: {project_id}")
        if ready:
            if mode != "verified":
                raise RecoveryGateError(
                    f"production-ready recovery contract must use verified mode: {project_id}"
                )
            if verification_level not in PRODUCTION_CAPABLE_LEVELS:
                raise RecoveryGateError(
                    f"production-ready recovery contract requires staging verification: {project_id}"
                )
            if not checkpoint or not rollback or not restore or not fault:
                raise RecoveryGateError(
                    f"production-ready recovery contract lacks evidence: {project_id}"
                )
        normalized = dict(item)
        normalized["checkpoint_evidence"] = checkpoint
        normalized["rollback_evidence"] = rollback
        normalized["restore_test_evidence"] = restore
        normalized["fault_injection_evidence"] = fault
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
    if item["verification_level"] not in PRODUCTION_CAPABLE_LEVELS:
        blockers.append("RECOVERY_VERIFICATION_LEVEL_INSUFFICIENT")
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
