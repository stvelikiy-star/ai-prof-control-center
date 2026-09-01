#!/usr/bin/env python3
"""Fail-closed code-readiness gate for reviewed Repair Team shadow activation.

This gate aggregates existing Repair Team safety evidence. It does not activate
systemd, change Git state, create production tasks, call an external AI model,
or grant repair authority. A PASS means only that the reviewed repository code
is internally ready for a separately approved shadow activation attempt.
It explicitly does NOT mean production-ready and does NOT verify live Ubuntu.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
ORCHESTRATOR = ROOT / "orchestrator"
for entry in (str(SCRIPT_DIR), str(ORCHESTRATOR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activate_repair_team_v2 as activation
import project_recovery_gate as recovery_gate
import repair_team_red_fault_injection as red_fault
import repair_team_shadow_fault_injection as yellow_fault

READINESS_VERSION = 1
READINESS_STATUS = "CODE_READY_FOR_REVIEWED_SHADOW_ACTIVATION"
PROJECT_ID = "ai-prof-control-center"
EXPECTED_CONTROL_POLICIES = {
    "runtime-checkout": "RED",
    "maintenance-checkout": "YELLOW",
    "supervisor-heartbeat": "YELLOW",
}


class ShadowReadinessError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ShadowReadinessError(f"required readiness file missing or unsafe: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ShadowReadinessError(f"invalid readiness JSON {path.name}: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ShadowReadinessError(f"readiness JSON must be an object: {path.name}")
    return payload


def verify_static_authority(root: Path) -> dict[str, Any]:
    config = _read_json(root / "orchestrator" / "config.json")
    if config.get("allow_merge") is not False:
        raise ShadowReadinessError("shadow readiness requires allow_merge=false")
    if config.get("allow_production_deploy") is not False:
        raise ShadowReadinessError("shadow readiness requires allow_production_deploy=false")
    if config.get("require_codex_pass") is not True:
        raise ShadowReadinessError("shadow readiness requires require_codex_pass=true")

    bindings = _read_json(root / "orchestrator" / "repair_operation_bindings.json")
    if bindings != {"version": 1, "bindings": []}:
        raise ShadowReadinessError("shadow readiness requires zero privileged repair bindings")

    policies = _read_json(root / "orchestrator" / "repair_policies.json")
    projects = policies.get("projects")
    if policies.get("version") != 1 or not isinstance(projects, dict):
        raise ShadowReadinessError("repair policy registry structure invalid")
    control = projects.get(PROJECT_ID)
    if control != EXPECTED_CONTROL_POLICIES:
        raise ShadowReadinessError("AI PROF shadow repair policy drifted")
    for project_id, mapping in projects.items():
        if not isinstance(mapping, dict):
            raise ShadowReadinessError(f"repair policy mapping invalid: {project_id}")
        if any(value == "GREEN" for value in mapping.values()):
            raise ShadowReadinessError(f"GREEN authority is forbidden during shadow readiness: {project_id}")
        if any(value not in {"RED", "YELLOW"} for value in mapping.values()):
            raise ShadowReadinessError(f"unexpected repair policy class during shadow readiness: {project_id}")

    return {
        "allow_merge": False,
        "allow_production_deploy": False,
        "privileged_bindings": 0,
        "green_authority": False,
        "control_policies": dict(control),
    }


def verify_shadow_recovery_contract(root: Path) -> dict[str, Any]:
    try:
        contracts = recovery_gate.load_recovery_contracts(root)
    except Exception as exc:
        raise ShadowReadinessError(f"recovery evidence validation failed: {exc}") from exc
    item = contracts.get(PROJECT_ID)
    if not isinstance(item, dict):
        raise ShadowReadinessError("AI PROF recovery contract missing")
    if item.get("recovery_mode") != "staged_activation":
        raise ShadowReadinessError("shadow readiness requires staged_activation recovery mode")
    if item.get("verification_level") != "shadow":
        raise ShadowReadinessError("shadow readiness requires shadow recovery verification level")
    if item.get("production_ready") is not False:
        raise ShadowReadinessError("shadow readiness must not claim production recovery readiness")
    if not item.get("checkpoint_evidence") or not item.get("rollback_evidence"):
        raise ShadowReadinessError("shadow readiness lacks checkpoint/rollback evidence")
    if not item.get("restore_test_evidence") or not item.get("fault_injection_evidence"):
        raise ShadowReadinessError("shadow readiness lacks restore/fault evidence")

    production_ready, blockers = recovery_gate.recovery_readiness(root, PROJECT_ID)
    if production_ready:
        raise ShadowReadinessError("shadow readiness unexpectedly passed production recovery gate")
    if "PRODUCTION_RECOVERY_NOT_APPROVED" not in blockers:
        raise ShadowReadinessError("production recovery approval blocker disappeared")

    return {
        "recovery_mode": item["recovery_mode"],
        "verification_level": item["verification_level"],
        "production_ready": False,
        "production_blockers": list(blockers),
        "fault_evidence_count": len(item["fault_injection_evidence"]),
    }


def _repository_unit_source(root: Path, name: str) -> Path:
    path = root / "systemd" / name
    if path.is_symlink() or not path.is_file():
        raise ShadowReadinessError(f"staged Repair Team unit missing or unsafe: {name}")
    return path


def verify_activation_v2_contract(root: Path) -> dict[str, Any]:
    previous_source = activation.base._unit_source
    try:
        activation.base._unit_source = lambda name: _repository_unit_source(root, name)
        activation.validate_staged_units()
    except Exception as exc:
        raise ShadowReadinessError(f"activation V2 staged-unit contract failed: {exc}") from exc
    finally:
        activation.base._unit_source = previous_source
    return {
        "activation_contract": "V2",
        "monitor_runners": list(activation.MONITOR_RUNNERS),
        "diagnosis_runners": list(activation.DIAGNOSIS_RUNNERS),
        "live_activation_performed": False,
    }


def _validate_yellow_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("status") != "PASS" or report.get("response_class") != "YELLOW":
        raise ShadowReadinessError("YELLOW shadow fault matrix did not PASS")
    if report.get("effective_next_action") != "PREPARE_REPAIR_FOR_OWNER_REVIEW":
        raise ShadowReadinessError("YELLOW shadow fault matrix action drifted")
    required_false = (
        "green_authority_granted",
        "external_ai_called",
        "production_queue_mutated",
        "target_repository_mutated",
        "real_state_mutated",
    )
    if any(report.get(key) is not False for key in required_false):
        raise ShadowReadinessError("YELLOW shadow fault matrix violated isolation/authority")
    if report.get("shadow_queue_health") != "healthy":
        raise ShadowReadinessError("YELLOW shadow fault matrix health is not healthy")
    return {
        "scenario_id": report.get("scenario_id"),
        "response_class": "YELLOW",
        "effective_next_action": report.get("effective_next_action"),
        "shadow_queue_health": "healthy",
    }


def _validate_red_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("status") != "PASS" or report.get("response_class") != "RED":
        raise ShadowReadinessError("RED shadow fault matrix did not PASS")
    if report.get("effective_next_action") != "OWNER_ACTION_REQUIRED":
        raise ShadowReadinessError("RED shadow fault matrix escaped owner-action boundary")
    required_false = (
        "repair_task_created",
        "bridge_blocked",
        "green_authority_granted",
        "external_ai_called",
        "production_queue_mutated",
        "target_repository_mutated",
        "real_state_mutated",
    )
    if any(report.get(key) is not False for key in required_false):
        raise ShadowReadinessError("RED shadow fault matrix violated terminal/isolation boundary")
    if report.get("owner_terminal_created") is not True:
        raise ShadowReadinessError("RED shadow fault matrix lacks owner terminal evidence")
    if report.get("shadow_queue_health") != "healthy":
        raise ShadowReadinessError("RED owner terminal degraded shadow health")
    return {
        "scenario_id": report.get("scenario_id"),
        "response_class": "RED",
        "effective_next_action": "OWNER_ACTION_REQUIRED",
        "owner_terminal_created": True,
        "shadow_queue_health": "healthy",
    }


def verify_fault_matrix(root: Path) -> dict[str, Any]:
    try:
        yellow_report = yellow_fault.run_shadow(root)
        red_report = red_fault.run_red_shadow(root)
    except Exception as exc:
        raise ShadowReadinessError(f"shadow fault matrix execution failed: {exc}") from exc
    return {
        "yellow": _validate_yellow_report(yellow_report),
        "red": _validate_red_report(red_report),
        "external_ai_called": False,
        "real_state_mutated": False,
    }


def build_readiness_report(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve(strict=True)
    authority = verify_static_authority(root)
    recovery = verify_shadow_recovery_contract(root)
    activation_contract = verify_activation_v2_contract(root)
    fault_matrix = verify_fault_matrix(root)
    return {
        "version": READINESS_VERSION,
        "status": "PASS",
        "readiness": READINESS_STATUS,
        "project_id": PROJECT_ID,
        "production_ready": False,
        "live_runtime_verified": False,
        "live_activation_performed": False,
        "authority": authority,
        "recovery": recovery,
        "activation": activation_contract,
        "fault_matrix": fault_matrix,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = build_readiness_report(args.root)
    except Exception as exc:
        payload = {
            "version": READINESS_VERSION,
            "status": "FAIL",
            "readiness": "NOT_READY",
            "production_ready": False,
            "live_runtime_verified": False,
            "error_type": type(exc).__name__,
            "error": " ".join(str(exc).split())[:1200],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"REPAIR_TEAM_SHADOW_READINESS: FAIL: {payload['error']}")
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"REPAIR_TEAM_SHADOW_READINESS: PASS: {READINESS_STATUS}")
        print("PRODUCTION_READY: false")
        print("LIVE_RUNTIME_VERIFIED: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
