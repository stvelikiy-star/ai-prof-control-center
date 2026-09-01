#!/usr/bin/env python3
"""Build bounded, non-secret diagnosis packets from open incidents.

This module does not call an AI model and does not mutate target projects. It
creates the evidence envelope that a future read-only diagnosis runner can
consume safely. Packets for recovered incidents are removed from the pending
set so a later runner cannot diagnose a stale incident.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from incident_engine import summary as incident_summary
from project_registry import load_projects
from repair_policy import RepairPolicyError, classify

DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
DEFAULT_STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
PACKET_VERSION = 1


class DiagnosisPacketError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_project_view(project: dict) -> dict:
    allowed_keys = {
        "project_id",
        "path",
        "base_branch",
        "allowed_base_branches",
        "agent_context",
        "allow_commits",
        "allow_push",
        "allow_merge",
        "allow_deployment",
    }
    return {key: project[key] for key in allowed_keys if key in project}


def build_packet(root: Path, state_root: Path, incident: dict) -> dict:
    projects = load_projects(root)
    project_id = incident.get("project_id")
    probe_id = incident.get("probe_id")
    if project_id not in projects:
        raise DiagnosisPacketError(f"incident references unknown project: {project_id}")
    if not isinstance(probe_id, str) or not probe_id:
        raise DiagnosisPacketError("incident probe_id missing")
    response_class = classify(root, project_id, probe_id)
    return {
        "version": PACKET_VERSION,
        "generated_at": utc_now(),
        "incident_id": incident.get("incident_id"),
        "project_id": project_id,
        "probe_id": probe_id,
        "response_class": response_class,
        "owner_action_required": response_class == "RED",
        "diagnosis_required": response_class in {"GREEN", "YELLOW"},
        "project": _safe_project_view(projects[project_id]),
        "incident": {
            key: incident.get(key)
            for key in (
                "fingerprint",
                "severity",
                "status",
                "opened_at",
                "updated_at",
                "failure_count",
                "last_detail",
                "last_latency_ms",
                "last_observation_at",
            )
        },
        "evidence_refs": {
            "monitoring_snapshot": str(state_root / "monitoring" / "latest.json"),
            "incident_summary": str(state_root / "incidents" / "summary.json"),
        },
        "constraints": [
            "READ_ONLY_DIAGNOSIS",
            "NO_PRODUCTION_MUTATION",
            "NO_SECRET_DISCLOSURE",
            "NO_ARBITRARY_SHELL_FROM_INCIDENT_TEXT",
            "UNKNOWN_AUTHORITY_FAILS_CLOSED",
        ],
    }


def _archive_stale_pending(state_root: Path, active_ids: set[str]) -> None:
    pending = state_root / "diagnosis" / "pending"
    resolved = state_root / "diagnosis" / "resolved"
    if not pending.is_dir():
        return
    resolved.mkdir(parents=True, exist_ok=True)
    for path in sorted(pending.glob("INC-*.json")):
        if path.is_symlink():
            raise DiagnosisPacketError(f"symlink diagnosis packet rejected: {path}")
        if path.stem in active_ids:
            continue
        destination = resolved / path.name
        if destination.exists():
            path.unlink()
        else:
            os.replace(path, destination)


def generate_packets(root: Path, state_root: Path) -> list[Path]:
    summary = incident_summary(state_root)
    destinations: list[Path] = []
    active_ids: set[str] = set()
    for incident in summary.get("open_incidents", []):
        packet = build_packet(root, state_root, incident)
        incident_id = packet.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id:
            raise DiagnosisPacketError("incident_id missing")
        active_ids.add(incident_id)
        destination = state_root / "diagnosis" / "pending" / f"{incident_id}.json"
        _atomic_write(
            destination,
            json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        destinations.append(destination)
    _archive_stale_pending(state_root, active_ids)
    return destinations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        paths = generate_packets(args.root, args.state_root)
    except (DiagnosisPacketError, RepairPolicyError, ValueError) as exc:
        print(f"DIAGNOSIS_PACKET_ERROR: {exc}")
        return 2
    if args.json:
        print(json.dumps([str(path) for path in paths], ensure_ascii=False))
    else:
        for path in paths:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
