"""Trusted project repair test contracts for AI PROF Repair Team.

Contracts do not introduce commands. For code repair they must match the
project's current `code_required_checks` in `projects.json` exactly. Any drift
between the two trusted sources blocks automatic repair-task creation.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from project_registry import load_projects, project_enabled

CONTRACT_VERSION = 1
CONTRACT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{5,79}$")
ALLOWED_KINDS = {"code_repair"}
REQUIRED_OUTCOME = "STAGE_01C_AUDIT_PASS"


class TestContractError(ValueError):
    pass


def _canonical_hash(contract: dict) -> str:
    canonical = {
        "version": CONTRACT_VERSION,
        "contract_id": contract["contract_id"],
        "project_id": contract["project_id"],
        "kind": contract["kind"],
        "required_checks": contract["required_checks"],
        "required_outcome": contract["required_outcome"],
    }
    raw = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _repair_capable_projects(projects: dict[str, dict]) -> set[str]:
    result: set[str] = set()
    for project_id, project in projects.items():
        if not project_enabled(project):
            continue
        checks = project.get("code_required_checks", [])
        if isinstance(checks, list) and checks:
            result.add(project_id)
    return result


def load_test_contracts(root: Path) -> dict[str, dict]:
    projects = load_projects(root)
    path = root / "orchestrator" / "project_test_contracts.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TestContractError(f"invalid project test contract file: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != CONTRACT_VERSION:
        raise TestContractError("invalid project test contract structure")
    raw_contracts = payload.get("contracts")
    if not isinstance(raw_contracts, list):
        raise TestContractError("project test contracts must be a list")

    by_project: dict[str, dict] = {}
    seen_ids: set[str] = set()
    required_keys = {
        "contract_id",
        "project_id",
        "kind",
        "required_checks",
        "required_outcome",
    }
    for item in raw_contracts:
        if not isinstance(item, dict) or set(item) != required_keys:
            raise TestContractError("invalid project test contract entry")
        contract_id = item.get("contract_id")
        project_id = item.get("project_id")
        kind = item.get("kind")
        checks = item.get("required_checks")
        outcome = item.get("required_outcome")
        if not isinstance(contract_id, str) or not CONTRACT_ID_RE.fullmatch(contract_id):
            raise TestContractError("invalid contract_id")
        if contract_id in seen_ids:
            raise TestContractError(f"duplicate contract_id: {contract_id}")
        seen_ids.add(contract_id)
        if project_id not in projects:
            raise TestContractError(f"test contract references unknown project: {project_id}")
        if project_id in by_project:
            raise TestContractError(f"multiple code test contracts for project: {project_id}")
        project = projects[project_id]
        if not project_enabled(project):
            raise TestContractError(f"test contract references disabled project: {project_id}")
        if kind not in ALLOWED_KINDS:
            raise TestContractError(f"invalid test contract kind: {contract_id}")
        if not isinstance(checks, list) or not checks or not all(
            isinstance(check, str) and check and check == check.strip() for check in checks
        ):
            raise TestContractError(f"invalid required_checks: {contract_id}")
        if len(set(checks)) != len(checks):
            raise TestContractError(f"duplicate required check: {contract_id}")
        registry_checks = project.get("code_required_checks", [])
        if checks != registry_checks:
            raise TestContractError(
                f"test contract drift from projects.json for {project_id}"
            )
        if outcome != REQUIRED_OUTCOME:
            raise TestContractError(f"invalid required_outcome: {contract_id}")
        normalized = dict(item)
        normalized["sha256"] = _canonical_hash(item)
        by_project[project_id] = normalized

    missing = sorted(_repair_capable_projects(projects) - set(by_project))
    if missing:
        raise TestContractError(
            "missing code test contract for repair-capable project(s): " + ", ".join(missing)
        )
    extra = sorted(set(by_project) - _repair_capable_projects(projects))
    if extra:
        raise TestContractError(
            "test contract exists for project without required code checks: " + ", ".join(extra)
        )
    return by_project


def contract_for_project(root: Path, project_id: str) -> dict:
    contracts = load_test_contracts(root)
    try:
        return contracts[project_id]
    except KeyError as exc:
        raise TestContractError(f"no trusted code test contract for project: {project_id}") from exc
