#!/usr/bin/env python3
"""Isolated RED-path fault injection for the AI PROF Repair Team.

This scenario reuses the already-audited shadow fault-injection primitives but
binds them to the real RED `runtime-checkout` policy. It proves that a RED
incident is diagnosed read-only, deterministically terminalized as
OWNER_ACTION_REQUIRED, creates no repair task or bridge blocker, keeps shadow
queue health healthy, and still resolves through two-cycle recovery.

No external AI, systemd mutation, deploy, merge, production queue mutation, or
target-project mutation occurs. All Repair Team state is temporary.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import repair_team_shadow_fault_injection as base

DEFAULT_PROJECT_ID = "ai-prof-control-center"
DEFAULT_PROBE_ID = "runtime-checkout"
SCENARIO_ID = "FI_REPAIR_TEAM_RED_OWNER_TERMINAL_V1"

FaultInjectionError = base.FaultInjectionError


def _status_function(
    root: Path,
    project_path: Path,
    supplied: Callable[[], str] | None,
) -> Callable[[], str]:
    if supplied is not None:
        return supplied
    return lambda: json.dumps(
        {
            "control_root": base._git_status(root),
            "target_project": base._git_status(project_path),
        },
        sort_keys=True,
    )


def _require_transition(
    state_root: Path,
    observation,
    expected: str,
    *,
    incident_id: str | None = None,
):
    transition, incident = base._reconcile_one(state_root, observation)
    if transition != expected:
        raise FaultInjectionError(
            f"unexpected hysteresis transition: expected={expected} actual={transition}"
        )
    if incident_id is None:
        return incident
    if incident is None or incident.incident_id != incident_id:
        raise FaultInjectionError(f"{expected} lost incident identity")
    return incident


def run_red_shadow(
    root: Path = ROOT,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    probe_id: str = DEFAULT_PROBE_ID,
    git_status_fn: Callable[[], str] | None = None,
) -> dict:
    root = root.resolve(strict=True)
    response_class, project_path = base._validate_route(root, project_id, probe_id)
    if response_class != "RED":
        raise FaultInjectionError(
            f"RED fault matrix requires RED policy, got {response_class}"
        )

    evidence_source = base.DEFAULT_EVIDENCE_SOURCE
    evidence_path = project_path / evidence_source
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise FaultInjectionError(f"RED evidence path unavailable: {evidence_source}")

    status_fn = _status_function(root, project_path, git_status_fn)
    before_status = status_fn()

    with tempfile.TemporaryDirectory(prefix="ai-prof-repair-red-fi-") as tmp:
        state_root = Path(tmp) / "state"
        state_root.mkdir()
        fake_codex = base._make_fake_codex(Path(tmp) / "fake-codex")

        first_failure = base._observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T01:00:00+00:00",
            detail="synthetic RED runtime failure #1",
        )
        first_incident = _require_transition(
            state_root, first_failure, "pending_failure"
        )
        if first_incident is not None:
            raise FaultInjectionError("first RED failure bypassed hysteresis")

        second_failure = base._observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T01:00:01+00:00",
            detail="synthetic RED runtime failure #2",
        )
        opened = _require_transition(state_root, second_failure, "opened")
        if opened is None or opened.failure_count != 1:
            raise FaultInjectionError("second RED failure did not open one incident")
        incident_id = opened.incident_id

        third_failure = base._observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T01:00:02+00:00",
            detail="synthetic RED runtime failure #3",
        )
        updated = _require_transition(
            state_root, third_failure, "updated", incident_id=incident_id
        )
        if updated.failure_count != 2:
            raise FaultInjectionError("repeat RED failure did not update same incident")

        open_summary = base.incident_engine.summary(state_root)
        if open_summary.get("open_count") != 1:
            raise FaultInjectionError("RED incident summary is not exactly one open incident")

        packets = base._generate_correlated_packets(root, state_root)
        if len(packets) != 1 or packets[0].stem != incident_id:
            raise FaultInjectionError("RED incident did not create one correlated packet")
        packet = json.loads(packets[0].read_text(encoding="utf-8"))
        if packet.get("response_class") != "RED":
            raise FaultInjectionError("RED diagnosis packet lost RED response class")
        correlation = packet.get("incident", {}).get("correlation")
        if (
            not isinstance(correlation, dict)
            or correlation.get("version") != 1
            or correlation.get("causal_inference") is not False
            or correlation.get("total_peer_count") != 0
        ):
            raise FaultInjectionError("RED correlation evidence contract invalid")

        codex_calls: list[dict] = []
        diagnosis_results = base.diagnosis_queue_drain.drain(
            root,
            state_root,
            codex_cli=fake_codex,
            invoke_fn=base._fake_codex_invoker(
                incident_id, evidence_source, codex_calls
            ),
        )
        if len(diagnosis_results) != 1 or diagnosis_results[0].status != "diagnosed":
            raise FaultInjectionError("RED diagnosis drain did not diagnose exactly one packet")
        if len(codex_calls) != 1 or codex_calls[0].get("prompt_has_contract") is not True:
            raise FaultInjectionError("RED diagnosis transport contract was not exercised once")

        diagnosis_path = state_root / "diagnosis" / "results" / f"{incident_id}.json"
        diagnosis_record = json.loads(diagnosis_path.read_text(encoding="utf-8"))
        if diagnosis_record.get("response_class") != "RED":
            raise FaultInjectionError("persisted RED diagnosis response class drifted")
        if diagnosis_record.get("effective_next_action") != "OWNER_ACTION_REQUIRED":
            raise FaultInjectionError("RED diagnosis escaped owner-action boundary")
        if diagnosis_record.get("eligible_runbooks") != []:
            raise FaultInjectionError("RED diagnosis unexpectedly gained runbook authority")

        bridge_results = base.repair_task_bridge_drain.drain(root, state_root)
        if len(bridge_results) != 1:
            raise FaultInjectionError("RED bridge drain cardinality invalid")
        bridge_result = bridge_results[0]
        if bridge_result.status != "owner_action_required" or bridge_result.task_id != "":
            raise FaultInjectionError("RED bridge did not terminalize owner action")
        terminal_path = Path(bridge_result.path)
        if not terminal_path.is_file() or state_root not in terminal_path.parents:
            raise FaultInjectionError("RED terminal evidence escaped temporary shadow state")
        terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        if (
            terminal.get("effective_next_action") != "OWNER_ACTION_REQUIRED"
            or terminal.get("response_class") != "RED"
            or terminal.get("task_created") is not False
            or terminal.get("owner_action_required") is not True
        ):
            raise FaultInjectionError("RED owner terminal evidence invalid")

        pending_tasks = list((state_root / "queue" / "pending").glob("*.md"))
        bridge_blocked = list((state_root / "repair_bridge" / "blocked").glob("*.json"))
        if pending_tasks:
            raise FaultInjectionError("RED owner terminal created a repair task")
        if bridge_blocked:
            raise FaultInjectionError("RED owner terminal created a bridge blocker")

        mid_snapshot = base.shadow_queue_health.build_snapshot(state_root)
        if mid_snapshot.get("ok") is not True:
            raise FaultInjectionError("valid RED owner terminal degraded shadow health")
        if mid_snapshot.get("bridge", {}).get("unprocessed_count") != 0:
            raise FaultInjectionError("RED terminal remained as bridge backlog")
        if mid_snapshot.get("bridge", {}).get("blocked_open_count") != 0:
            raise FaultInjectionError("RED terminal remained as bridge blocker")

        # Stage one fresh packet while the incident remains open. Recovery must
        # archive it after the second healthy observation, proving stale cleanup.
        regenerated = base._generate_correlated_packets(root, state_root)
        if len(regenerated) != 1 or regenerated[0].stem != incident_id:
            raise FaultInjectionError("could not stage RED stale-packet recovery proof")

        first_recovery = base._observation(
            project_id,
            probe_id,
            ok=True,
            checked_at="2026-09-01T01:00:03+00:00",
            detail="synthetic RED runtime recovery #1",
        )
        _require_transition(
            state_root, first_recovery, "pending_recovery", incident_id=incident_id
        )

        second_recovery = base._observation(
            project_id,
            probe_id,
            ok=True,
            checked_at="2026-09-01T01:00:04+00:00",
            detail="synthetic RED runtime recovery #2",
        )
        resolved = _require_transition(
            state_root, second_recovery, "resolved", incident_id=incident_id
        )
        if resolved.status != "resolved":
            raise FaultInjectionError("RED incident did not resolve")

        post_recovery_packets = base._generate_correlated_packets(root, state_root)
        if post_recovery_packets:
            raise FaultInjectionError("resolved RED incident still produced diagnosis packet")
        archived = state_root / "diagnosis" / "resolved" / f"{incident_id}.json"
        if not archived.is_file():
            raise FaultInjectionError("RED stale packet was not archived")

        closed_summary = base.incident_engine.summary(state_root)
        if closed_summary.get("open_count") != 0 or closed_summary.get("resolved_count") != 1:
            raise FaultInjectionError("resolved RED incident summary inconsistent")

        final_snapshot = base.shadow_queue_health.build_snapshot(state_root)
        final_path = base.shadow_queue_health.write_snapshot(state_root, final_snapshot)
        health_ok, health_detail = base.shadow_queue_health.read_health(state_root)
        if final_snapshot.get("ok") is not True or not health_ok:
            raise FaultInjectionError(f"final RED shadow health failed: {health_detail}")
        if not final_path.is_file():
            raise FaultInjectionError("final RED shadow health evidence missing")

    after_status = status_fn()
    if before_status != after_status:
        raise FaultInjectionError("repository working tree changed during RED shadow fault injection")

    return {
        "version": 1,
        "scenario_id": SCENARIO_ID,
        "status": "PASS",
        "project_id": project_id,
        "probe_id": probe_id,
        "response_class": response_class,
        "incident_id": incident_id,
        "transitions": [
            "pending_failure",
            "opened",
            "updated",
            "pending_recovery",
            "resolved",
        ],
        "correlation_evidence_validated": True,
        "diagnosis_protocol_validated": True,
        "diagnosis_drain_validated": True,
        "effective_next_action": "OWNER_ACTION_REQUIRED",
        "owner_terminal_created": True,
        "repair_task_created": False,
        "bridge_blocked": False,
        "green_authority_granted": False,
        "shadow_queue_health": "healthy",
        "stale_packet_archived": True,
        "external_ai_called": False,
        "production_queue_mutated": False,
        "target_repository_mutated": False,
        "real_state_mutated": False,
        "verification_level": "shadow",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--project", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--probe", default=DEFAULT_PROBE_ID)
    args = parser.parse_args()
    try:
        report = run_red_shadow(
            args.root,
            project_id=args.project,
            probe_id=args.probe,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "scenario_id": SCENARIO_ID,
                    "error": str(exc)[:1000],
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
