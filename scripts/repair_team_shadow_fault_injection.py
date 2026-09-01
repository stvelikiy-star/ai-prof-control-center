#!/usr/bin/env python3
"""Isolated fault injection for the AI PROF Repair Team incident lifecycle.

The scenario never stops a service, edits a project, deploys code, or touches the
real AI PROF state directory. It injects synthetic monitoring observations into
a temporary state root while using the real incident and diagnosis-packet code.
The target repository Git status is compared before/after to detect accidental
mutation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_packet
import incident_engine
import monitoring_engine as monitor
from monitoring_profiles import load_monitoring_profiles
from project_registry import load_projects, project_enabled
from repair_policy import classify

DEFAULT_PROJECT_ID = "ai-prof-control-center"
DEFAULT_PROBE_ID = "maintenance-checkout"
SCENARIO_ID = "FI_REPAIR_TEAM_INCIDENT_LIFECYCLE_V1"


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


def _validate_route(root: Path, project_id: str, probe_id: str) -> str:
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
    return classify(root, project_id, probe_id)


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


def run_shadow(
    root: Path = ROOT,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    probe_id: str = DEFAULT_PROBE_ID,
    git_status_fn: Callable[[], str] | None = None,
) -> dict:
    root = root.resolve(strict=True)
    response_class = _validate_route(root, project_id, probe_id)
    status_fn = git_status_fn or (lambda: _git_status(root))
    before_status = status_fn()

    with tempfile.TemporaryDirectory(prefix="ai-prof-repair-fi-") as tmp:
        state_root = Path(tmp)
        first_failure = _observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T00:00:00+00:00",
            detail="synthetic shadow failure #1",
        )
        opened_transition, opened = incident_engine.apply_observation(state_root, first_failure)
        if opened_transition != "opened" or opened is None or opened.failure_count != 1:
            raise FaultInjectionError("first failure did not open exactly one incident")

        second_failure = _observation(
            project_id,
            probe_id,
            ok=False,
            checked_at="2026-09-01T00:00:01+00:00",
            detail="synthetic shadow failure #2",
        )
        updated_transition, updated = incident_engine.apply_observation(state_root, second_failure)
        if (
            updated_transition != "updated"
            or updated is None
            or updated.incident_id != opened.incident_id
            or updated.failure_count != 2
        ):
            raise FaultInjectionError("repeat failure created duplicate or invalid incident state")

        incident_engine.write_summary(state_root)
        open_summary = incident_engine.summary(state_root)
        if open_summary.get("open_count") != 1:
            raise FaultInjectionError("open incident summary count is not one")

        packets = diagnosis_packet.generate_packets(root, state_root)
        if len(packets) != 1 or packets[0].stem != opened.incident_id:
            raise FaultInjectionError("open incident did not create exactly one diagnosis packet")
        packet = json.loads(packets[0].read_text(encoding="utf-8"))
        if (
            packet.get("incident_id") != opened.incident_id
            or packet.get("project_id") != project_id
            or packet.get("probe_id") != probe_id
            or packet.get("response_class") != response_class
            or packet.get("constraints", []).count("NO_PRODUCTION_MUTATION") != 1
        ):
            raise FaultInjectionError("diagnosis packet binding or safety constraints are invalid")

        recovery = _observation(
            project_id,
            probe_id,
            ok=True,
            checked_at="2026-09-01T00:00:02+00:00",
            detail="synthetic shadow recovery",
        )
        resolved_transition, resolved = incident_engine.apply_observation(state_root, recovery)
        if (
            resolved_transition != "resolved"
            or resolved is None
            or resolved.incident_id != opened.incident_id
            or resolved.status != "resolved"
        ):
            raise FaultInjectionError("recovery did not resolve the injected incident")
        incident_engine.write_summary(state_root)

        post_recovery_packets = diagnosis_packet.generate_packets(root, state_root)
        if post_recovery_packets:
            raise FaultInjectionError("recovered incident still produced a diagnosis packet")
        archived_packet = state_root / "diagnosis" / "resolved" / f"{opened.incident_id}.json"
        if not archived_packet.is_file():
            raise FaultInjectionError("stale diagnosis packet was not archived after recovery")
        closed_summary = incident_engine.summary(state_root)
        if closed_summary.get("open_count") != 0 or closed_summary.get("resolved_count") != 1:
            raise FaultInjectionError("resolved incident summary is inconsistent")
        if (state_root / "queue").exists():
            raise FaultInjectionError("shadow fault injection unexpectedly created execution queue state")

        incident_id = opened.incident_id

    after_status = status_fn()
    if before_status != after_status:
        raise FaultInjectionError("repository working tree changed during shadow fault injection")

    return {
        "version": 1,
        "scenario_id": SCENARIO_ID,
        "status": "PASS",
        "project_id": project_id,
        "probe_id": probe_id,
        "response_class": response_class,
        "incident_id": incident_id,
        "transitions": ["opened", "updated", "resolved"],
        "duplicate_suppressed": True,
        "diagnosis_packet_created": True,
        "stale_packet_archived": True,
        "execution_queue_created": False,
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
