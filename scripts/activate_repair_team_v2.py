#!/usr/bin/env python3
"""Activation contract V2 for the current AI PROF Repair Team shadow stack.

V2 deliberately reuses V1's already-tested unit checkpoint, installation,
state restoration and automatic rollback implementation. It replaces only the
staged-unit contract and diagnosis post-activation evidence contract for the
current canary/drain/health entrypoints. The overrides live only for one
``base.activate`` call and are restored in ``finally``.

This script does not merge, deploy customer code, run migrations, enable GREEN
repair authority, or switch the live Git checkout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR / "activate_repair_team_v1.py"
BASE_SPEC = importlib.util.spec_from_file_location("activate_repair_team_v1_for_v2", BASE_PATH)
if BASE_SPEC is None or BASE_SPEC.loader is None:
    raise RuntimeError("cannot load Repair Team activation V1 core")
base = importlib.util.module_from_spec(BASE_SPEC)
sys.modules[BASE_SPEC.name] = base
BASE_SPEC.loader.exec_module(base)

ActivationError = base.ActivationError

MONITOR_RUNNERS = (
    "incident_engine_shadow_health_canary.py",
    "diagnosis_packet_canary.py",
)
DIAGNOSIS_RUNNERS = (
    "diagnosis_queue_drain.py",
    "repair_task_bridge_drain.py",
    "shadow_queue_health.py",
)
SHADOW_HEALTH_RELATIVE = Path("shadow_queue_health/latest.json")
SHADOW_HEALTH_VERSION = 1
SHADOW_HEALTH_KEYS = {
    "version",
    "timestamp",
    "ok",
    "state",
    "thresholds",
    "diagnosis",
    "bridge",
    "reasons",
}


def _expected_exec_lines(runners: tuple[str, ...]) -> list[str]:
    return [
        f"ExecStart=/usr/bin/python3 {base.LIVE}/orchestrator/{runner}"
        for runner in runners
    ]


def _exec_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("ExecStart=")]


def validate_staged_units() -> None:
    """Require the exact current passive monitor and shadow diagnosis stack."""
    monitor = base._unit_source(base.MONITOR_SERVICE).read_text(encoding="utf-8")
    diagnosis = base._unit_source(base.DIAGNOSIS_SERVICE).read_text(encoding="utf-8")
    monitor_timer = base._unit_source(base.MONITOR_TIMER).read_text(encoding="utf-8")
    diagnosis_timer = base._unit_source(base.DIAGNOSIS_TIMER).read_text(encoding="utf-8")

    expected_monitor = _expected_exec_lines(MONITOR_RUNNERS)
    expected_diagnosis = _expected_exec_lines(DIAGNOSIS_RUNNERS)
    if _exec_lines(monitor) != expected_monitor:
        raise ActivationError(
            f"monitor unit runner contract mismatch: expected={expected_monitor!r}, actual={_exec_lines(monitor)!r}"
        )
    if _exec_lines(diagnosis) != expected_diagnosis:
        raise ActivationError(
            "diagnosis unit runner contract mismatch: "
            f"expected={expected_diagnosis!r}, actual={_exec_lines(diagnosis)!r}"
        )

    for name, text in ((base.MONITOR_SERVICE, monitor), (base.DIAGNOSIS_SERVICE, diagnosis)):
        required_hardening = ("NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=read-only")
        if any(token not in text for token in required_hardening):
            raise ActivationError(f"Repair Team unit lost filesystem/process hardening: {name}")
        expected_rw = f"ReadWritePaths={base.STATE_ROOT}"
        rw_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("ReadWritePaths=")
        ]
        if rw_lines != [expected_rw]:
            raise ActivationError(f"Repair Team unit write boundary drifted: {name}: {rw_lines}")
        forbidden = (
            "operations_runner.py",
            "repair_operations_bridge.py",
            "incident_operation_execution_gate.py",
            "approved_task_publisher",
            "release_flow.py",
            "supabase db push",
            "docker restart",
            "git push",
        )
        if any(token in text for token in forbidden):
            raise ActivationError(f"privileged runner leaked into staged Repair Team unit: {name}")

    if f"Unit={base.MONITOR_SERVICE}" not in monitor_timer:
        raise ActivationError("monitor timer targets unexpected service")
    if f"Unit={base.DIAGNOSIS_SERVICE}" not in diagnosis_timer:
        raise ActivationError("diagnosis timer targets unexpected service")


def _fresh_age_seconds(raw_timestamp: str, path: Path) -> float:
    try:
        instant = dt.datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActivationError(f"shadow queue health timestamp invalid: {path.name}") from exc
    if instant.tzinfo is None:
        raise ActivationError(f"shadow queue health timestamp is naive: {path.name}")
    age = (dt.datetime.now(dt.timezone.utc) - instant.astimezone(dt.timezone.utc)).total_seconds()
    if age < -30 or age > base.FRESH_EVIDENCE_SECONDS:
        raise ActivationError(f"shadow queue health is stale: {path.name}: age={age:.1f}s")
    return age


def _nonnegative_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ActivationError(f"shadow queue health counter invalid: {field}")
    return value


def verify_shadow_queue_health_evidence() -> dict[str, Any]:
    path = base.STATE_ROOT / SHADOW_HEALTH_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ActivationError("shadow queue health evidence is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ActivationError(f"invalid shadow queue health evidence: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or set(payload) != SHADOW_HEALTH_KEYS:
        raise ActivationError("shadow queue health schema mismatch")
    if payload.get("version") != SHADOW_HEALTH_VERSION:
        raise ActivationError("shadow queue health version mismatch")
    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, str):
        raise ActivationError("shadow queue health timestamp missing")
    age = _fresh_age_seconds(timestamp, path)
    if payload.get("ok") is not True or payload.get("state") != "healthy":
        raise ActivationError("shadow queue health is degraded after diagnosis activation")
    if not isinstance(payload.get("thresholds"), dict):
        raise ActivationError("shadow queue health thresholds invalid")
    diagnosis = payload.get("diagnosis")
    bridge = payload.get("bridge")
    if not isinstance(diagnosis, dict) or not isinstance(bridge, dict):
        raise ActivationError("shadow queue health queue summaries invalid")
    reasons = payload.get("reasons")
    if reasons != []:
        raise ActivationError("healthy shadow queue health contains degradation reasons")

    diagnosis_pending = _nonnegative_count(diagnosis.get("pending_count"), "diagnosis.pending_count")
    diagnosis_blocked = _nonnegative_count(
        diagnosis.get("blocked_open_count"), "diagnosis.blocked_open_count"
    )
    bridge_unprocessed = _nonnegative_count(
        bridge.get("unprocessed_count"), "bridge.unprocessed_count"
    )
    bridge_blocked = _nonnegative_count(
        bridge.get("blocked_open_count"), "bridge.blocked_open_count"
    )
    return {
        "state": "healthy",
        "age_seconds": round(age, 3),
        "diagnosis_pending": diagnosis_pending,
        "diagnosis_blocked_open": diagnosis_blocked,
        "bridge_unprocessed": bridge_unprocessed,
        "bridge_blocked_open": bridge_blocked,
    }


def _require_timer_ready(timer: str) -> dict[str, str]:
    enabled = base.systemd_state("is-enabled", timer)
    active = base.systemd_state("is-active", timer)
    if enabled != "enabled" or active != "active":
        raise ActivationError(
            f"Repair Team timer not ready after activation: {timer}: enabled={enabled}, active={active}"
        )
    return {"enabled": enabled, "active": active}


def verify_post_activation(approved_sha: str, phase: str, before_status: str) -> dict[str, Any]:
    if base.git("rev-parse", "HEAD") != approved_sha or base.git("branch", "--show-current") != "main":
        raise ActivationError("Repair Team activation changed live Git identity")
    if base.git("status", "--porcelain") != before_status:
        raise ActivationError("Repair Team activation changed live worktree")

    monitor_timer = _require_timer_ready(base.MONITOR_TIMER)
    monitor = base.verify_monitor_evidence()
    if phase == "monitor":
        return {
            "monitor_timer": monitor_timer,
            "monitor": monitor,
        }

    diagnosis_timer = _require_timer_ready(base.DIAGNOSIS_TIMER)
    base.verify_zero_privileged_bindings()
    shadow = verify_shadow_queue_health_evidence()
    return {
        "monitor_timer": monitor_timer,
        "diagnosis_timer": diagnosis_timer,
        "privileged_bindings": 0,
        "monitor": monitor,
        "shadow_queue_health": shadow,
    }


def activate(approved_sha: str, phase: str):
    """Run V1 activation mechanics under the V2 validation/evidence contract."""
    previous_validate = base.validate_staged_units
    previous_post = base.verify_post_activation
    try:
        base.validate_staged_units = validate_staged_units
        base.verify_post_activation = verify_post_activation
        return base.activate(approved_sha, phase)
    finally:
        base.validate_staged_units = previous_validate
        base.verify_post_activation = previous_post


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-sha", required=True)
    parser.add_argument("--phase", required=True, choices=tuple(base.PHASE_UNITS))
    args = parser.parse_args()
    try:
        checkpoint, evidence = activate(args.approved_sha.strip(), args.phase)
    except ActivationError as exc:
        print(f"REPAIR_TEAM_ACTIVATION_V2: BLOCKED: {exc}")
        return 2
    print("REPAIR_TEAM_ACTIVATION_V2: PASS")
    print(f"PHASE: {args.phase}")
    print(f"ACTIVE_SHA: {base.git('rev-parse', 'HEAD')}")
    print(f"ROLLBACK_CHECKPOINT: {checkpoint}")
    print("EVIDENCE: " + json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
