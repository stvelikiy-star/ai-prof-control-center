#!/usr/bin/env python3
"""Isolated end-to-end fault injection for the current Repair Team shadow stack.

The scenario exercises the real two-cycle incident hysteresis, correlation
packet builder, diagnosis protocol/result persistence, bounded diagnosis drain,
validated repair-task bridge, stale packet archive, and shadow queue health.
Only the external Codex transport is replaced with deterministic JSON; no model,
service, deployment, merge, production queue, or target-project mutation occurs.
All mutable state is rooted in a TemporaryDirectory.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_packet_canary
import diagnosis_queue_drain
import incident_engine
import incident_engine_canary
import monitoring_engine as monitor
import repair_task_bridge_drain
import shadow_queue_health
from monitoring_profiles import load_monitoring_profiles
from project_registry import load_projects, project_enabled
from repair_policy import classify

DEFAULT_PROJECT_ID = "ai-prof-control-center"
DEFAULT_PROBE_ID = "maintenance-checkout"
DEFAULT_EVIDENCE_SOURCE = "orchestrator/telegram_bridge.py"
SCENARIO_ID = "FI_REPAIR_TEAM_SHADOW_PIPELINE_V2"


class FaultInjectionError(RuntimeError):
    pass


def _git_status(root: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git status failed").strip()
        raise FaultInjectionError(f"cannot verify repository immutability: {detail[:500]}")
    return result.stdout


def _validate_route(root: Path, project_id: str, probe_id: str) -> tuple[str, Path]:
    projects = load_projects(root)
    project = projects.get(project_id)
    if project is None or not project_enabled(project):
        raise FaultInjectionError(f"fault-injection project unavailable: {project_id}")
    profiles = load_monitoring_profiles(root, set(projects))
    profile = profiles.get(project_id)
    if not isinstance(profile, dict) or profile.get("enabled") is not True:
        raise FaultInjectionError(f"monitoring profile disabled: {project_id}")
    probes = profile.get("probes")
    if not isinstance(probes, list):
        raise FaultInjectionError(f"monitoring probes invalid: {project_id}")
    matches = [item for item in probes if isinstance(item, dict) and item.get("id") == probe_id]
    if len(matches) != 1:
        raise FaultInjectionError(f"fault-injection probe is not uniquely registered: {project_id}:{probe_id}")
    project_path = Path(str(project.get("path", ""))).resolve(strict=False)
    if not project_path.is_absolute() or not project_path.is_dir():
        raise FaultInjectionError(f"fault-injection project path unavailable: {project_id}")
    return classify(root, project_id, probe_id), project_path


def _observation(project_id: str, probe_id: str, *, ok: bool, checked_at: str, detail: str) -> monitor.Observation:
    return monitor.Observation(
        project_id=project_id,
        probe_id=probe_id,
        kind="fault_injection_shadow",
        severity="warning",
        ok=ok,
        checked_at=checked_at,
        latency_ms=1,
        detail=detail,
        fingerprint=f"{project_id}:{probe_id}",
    )


def _reconcile_one(state_root: Path, observation: monitor.Observation):
    result = incident_engine_canary.reconcile(state_root, [observation])
    if len(result) != 1:
        raise FaultInjectionError("hysteresis reconcile returned unexpected cardinality")
    incident_engine.write_summary(state_root)
    return result[0]


def _generate_correlated_packets(root: Path, state_root: Path) -> list[Path]:
    base = diagnosis_packet_canary.base
    original = base.build_packet
    try:
        base.build_packet = diagnosis_packet_canary.build_packet
        return base.generate_packets(root, state_root)
    finally:
        base.build_packet = original


def _fake_codex_invoker(incident_id: str, evidence_source: str, calls: list[dict]):
    def invoke(codex_cli: Path, project_path: Path, prompt: str) -> subprocess.CompletedProcess:
        if "READ_ONLY_DIAGNOSIS" not in prompt or "NO_PRODUCTION_MUTATION" not in prompt:
            raise FaultInjectionError("trusted diagnosis prompt lost required constraints")
        calls.append(
            {
                "codex_cli": str(codex_cli),
                "project_path": str(project_path),
                "prompt_has_contract": "Required JSON contract:" in prompt,
            }
        )
        payload = {
            "version": 1,
            "incident_id": incident_id,
            "root_cause": "Synthetic checkout fault bound to the injected warning observation.",
            "confidence": 0.88,
            "repairable": True,
            "evidence": [
                {
                    "source": evidence_source,
                    "finding": "Existing allowlisted code evidence path used only for shadow task preparation.",
                }
            ],
            "suggested_action": "CODE_REPAIR",
            "residual_risks": ["Synthetic scenario; no production mutation is permitted."],
        }
        return subprocess.CompletedProcess(
            args=[str(codex_cli)],
            returncode=0,
            stdout=json.dumps(payload, ensure_ascii=False),
            stderr="",
        )

    return invoke


def _make_fake_codex(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    path.chmod(0o700)
    if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
        raise FaultInjectionError("cannot create isolated fake Codex executable")
    return path


def run_shadow(
    root: Path = ROOT,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    probe_id: str = DEFAULT_PROBE_ID,
    git_status_fn: Callable[[], str] | None = None,
) -> dict:
    root = root.resolve(strict=True)
    response_class, project_path = _validate_route(root, project_id, probe_id)
    if response_class != "YELLOW":
        raise FaultInjectionError(
            f"V2 shadow repair-path scenario requires YELLOW policy, got {response_class}"
        )
    evidence_path = project_path / DEFAULT_EVIDENCE_SOURCE
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise FaultInjectionError(f"fault-injection evidence path unavailable: {DEFAULT_EVIDENCE_SOURCE}")

    if git_status_fn is None:
        status_fn = lambda: json.dumps(
            {
                "control_root": _git_status(root),
                "target_project": _git_status(project_path),
            },
            sort_keys=True,
        )
    else:
        status_fn = git_status_fn
    before_status = status_fn()

    with tempfile.TemporaryDirectory(prefix="ai-prof-repair-fi-v2-") as tmp:
        state_root = Path(tmp) / "state"
        state_root.mkdir()
        fake_codex = _make_fake_codex(Path(tmp) / "fake-codex")

        first_failure = _observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T00:00:00+00:00",
            detail="synthetic shadow failure #1",
        )
        first_transition, first_incident = _reconcile_one(state_root, first_failure)
        if first_transition != "pending_failure" or first_incident is not None:
            raise FaultInjectionError("first failure bypassed two-cycle hysteresis")

        second_failure = _observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T00:00:01+00:00",
            detail="synthetic shadow failure #2",
        )
        opened_transition, opened = _reconcile_one(state_root, second_failure)
        if opened_transition != "opened" or opened is None or opened.failure_count != 1:
            raise FaultInjectionError("second failure did not open exactly one incident")

        third_failure = _observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T00:00:02+00:00",
            detail="synthetic shadow failure #3",
        )
        updated_transition, updated = _reconcile_one(state_root, third_failure)
        if (
            updated_transition != "updated"
            or updated is None
            or updated.incident_id != opened.incident_id
            or updated.failure_count != 2
        ):
            raise FaultInjectionError("repeat failure created duplicate or invalid incident state")

        open_summary = incident_engine.summary(state_root)
        if open_summary.get("open_count") != 1:
            raise FaultInjectionError("open incident summary count is not one")

        packets = _generate_correlated_packets(root, state_root)
        if len(packets) != 1 or packets[0].stem != opened.incident_id:
            raise FaultInjectionError("open incident did not create exactly one diagnosis packet")
        packet = json.loads(packets[0].read_text(encoding="utf-8"))
        correlation = packet.get("incident", {}).get("correlation")
        if not isinstance(correlation, dict):
            raise FaultInjectionError("diagnosis packet missing correlation evidence")
        if (
            correlation.get("version") != 1
            or correlation.get("basis") != "same_project_open_incidents"
            or correlation.get("causal_inference") is not False
            or correlation.get("total_peer_count") != 0
        ):
            raise FaultInjectionError("diagnosis correlation contract is invalid")
        if packet.get("response_class") != response_class:
            raise FaultInjectionError("diagnosis packet response class drifted")

        codex_calls: list[dict] = []
        diagnosis_results = diagnosis_queue_drain.drain(
            root,
            state_root,
            codex_cli=fake_codex,
            invoke_fn=_fake_codex_invoker(opened.incident_id, DEFAULT_EVIDENCE_SOURCE, codex_calls),
        )
        if len(diagnosis_results) != 1 or diagnosis_results[0].status != "diagnosed":
            raise FaultInjectionError("bounded diagnosis drain did not diagnose exactly one packet")
        if len(codex_calls) != 1 or codex_calls[0].get("prompt_has_contract") is not True:
            raise FaultInjectionError("diagnosis transport was not invoked exactly once with strict contract")
        diagnosis_record = json.loads(
            (state_root / "diagnosis" / "results" / f"{opened.incident_id}.json").read_text(encoding="utf-8")
        )
        if diagnosis_record.get("effective_next_action") != "PREPARE_REPAIR_FOR_OWNER_REVIEW":
            raise FaultInjectionError("YELLOW diagnosis escaped owner-review preparation boundary")
        if diagnosis_record.get("eligible_runbooks") != []:
            raise FaultInjectionError("YELLOW diagnosis unexpectedly gained GREEN runbook authority")

        bridge_results = repair_task_bridge_drain.drain(root, state_root)
        if len(bridge_results) != 1 or bridge_results[0].status != "created":
            raise FaultInjectionError("repair bridge did not create exactly one shadow task")
        bridge_result = bridge_results[0]
        shadow_task = Path(bridge_result.path)
        if not shadow_task.is_file() or state_root not in shadow_task.parents:
            raise FaultInjectionError("repair bridge task escaped temporary shadow state")
        if DEFAULT_EVIDENCE_SOURCE not in shadow_task.read_text(encoding="utf-8"):
            raise FaultInjectionError("shadow repair task lost validated evidence scope")

        mid_snapshot = shadow_queue_health.build_snapshot(state_root)
        if mid_snapshot.get("ok") is not True:
            raise FaultInjectionError("healthy processed shadow queues reported degraded")
        if mid_snapshot.get("diagnosis", {}).get("pending_count") != 0:
            raise FaultInjectionError("diagnosis queue not drained")
        if mid_snapshot.get("bridge", {}).get("unprocessed_count") != 0:
            raise FaultInjectionError("repair bridge result remained unprocessed")

        # Recreate one valid packet while the incident is still open so the
        # subsequent 2-cycle recovery must archive it as stale.
        regenerated = _generate_correlated_packets(root, state_root)
        if len(regenerated) != 1:
            raise FaultInjectionError("could not stage stale-packet recovery proof")

        first_recovery = _observation(
            project_id,
            probe_id,
            ok=True,
            checked_at="2026-09-01T00:00:03+00:00",
            detail="synthetic shadow recovery #1",
        )
        recovery_transition, recovery_incident = _reconcile_one(state_root, first_recovery)
        if (
            recovery_transition != "pending_recovery"
            or recovery_incident is None
            or recovery_incident.incident_id != opened.incident_id
        ):
            raise FaultInjectionError("first recovery bypassed two-cycle hysteresis")

        second_recovery = _observation(
            project_id,
            probe_id,
            ok=True,
            checked_at="2026-09-01T00:00:04+00:00",
            detail="synthetic shadow recovery #2",
        )
        resolved_transition, resolved = _reconcile_one(state_root, second_recovery)
        if (
            resolved_transition != "resolved"
            or resolved is None
            or resolved.incident_id != opened.incident_id
            or resolved.status != "resolved"
        ):
            raise FaultInjectionError("second recovery did not resolve injected incident")

        post_recovery_packets = _generate_correlated_packets(root, state_root)
        if post_recovery_packets:
            raise FaultInjectionError("recovered incident still produced a diagnosis packet")
        archived_packet = state_root / "diagnosis" / "resolved" / f"{opened.incident_id}.json"
        if not archived_packet.is_file():
            raise FaultInjectionError("stale diagnosis packet was not archived after recovery")
        closed_summary = incident_engine.summary(state_root)
        if closed_summary.get("open_count") != 0 or closed_summary.get("resolved_count") != 1:
            raise FaultInjectionError("resolved incident summary is inconsistent")

        final_snapshot = shadow_queue_health.build_snapshot(state_root)
        final_health_path = shadow_queue_health.write_snapshot(state_root, final_snapshot)
        health_ok, health_detail = shadow_queue_health.read_health(state_root)
        if final_snapshot.get("ok") is not True or not health_ok:
            raise FaultInjectionError(f"final shadow queue health failed: {health_detail}")
        if not final_health_path.is_file():
            raise FaultInjectionError("final shadow queue health evidence was not persisted")

        incident_id = opened.incident_id
        shadow_task_id = bridge_result.task_id

    after_status = status_fn()
    if before_status != after_status:
        raise FaultInjectionError("repository working tree changed during shadow fault injection")

    return {
        "version": 2,
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
        "duplicate_suppressed": True,
        "correlation_evidence_validated": True,
        "diagnosis_protocol_validated": True,
        "diagnosis_drain_validated": True,
        "effective_next_action": "PREPARE_REPAIR_FOR_OWNER_REVIEW",
        "green_authority_granted": False,
        "shadow_repair_task_created": True,
        "shadow_task_id": shadow_task_id,
        "bridge_drain_validated": True,
        "stale_packet_archived": True,
        "shadow_queue_health": "healthy",
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
        report = run_shadow(args.root, project_id=args.project, probe_id=args.probe)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "scenario_id": SCENARIO_ID, "error": str(exc)[:1000]}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
