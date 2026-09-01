#!/usr/bin/env python3
"""Deterministic incident lifecycle for AI PROF monitoring observations.

One failing observation fingerprint maps to one open incident. Repeated failures
update evidence instead of creating alert storms. A recovery observation moves
the incident to immutable episode history. This module does not repair or
deploy anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from monitoring_engine import (
    DEFAULT_ROOT,
    DEFAULT_STATE_ROOT,
    Observation,
    MonitoringConfigError,
    monitor_projects,
    write_snapshot,
)
from project_registry import ProjectPolicyError

INCIDENT_VERSION = 1


@dataclass
class Incident:
    version: int
    incident_id: str
    fingerprint: str
    project_id: str
    probe_id: str
    severity: str
    status: str
    opened_at: str
    updated_at: str
    resolved_at: str
    failure_count: int
    last_detail: str
    last_latency_ms: int
    last_observation_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def incident_id_for(observation: Observation, opened_at: str) -> str:
    episode_key = f"{observation.fingerprint}|{opened_at}"
    digest = hashlib.sha256(episode_key.encode("utf-8")).hexdigest()[:10].upper()
    project = "".join(ch for ch in observation.project_id.upper() if ch.isalnum())[:16] or "PROJECT"
    return f"INC-{project}-{digest}"


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


def _load(path: Path) -> Incident | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"invalid incident state {path}: {exc}") from exc
    required = {
        "version",
        "incident_id",
        "fingerprint",
        "project_id",
        "probe_id",
        "severity",
        "status",
        "opened_at",
        "updated_at",
        "resolved_at",
        "failure_count",
        "last_detail",
        "last_latency_ms",
        "last_observation_at",
    }
    if not isinstance(raw, dict) or set(raw) != required or raw.get("version") != INCIDENT_VERSION:
        raise RuntimeError(f"invalid incident schema: {path}")
    return Incident(**raw)


def _open_path(state_root: Path, fingerprint: str) -> Path:
    return state_root / "incidents" / "open" / f"{_safe_key(fingerprint)}.json"


def _resolved_path(state_root: Path, incident_id: str) -> Path:
    return state_root / "incidents" / "resolved" / f"{incident_id}.json"


def _save(path: Path, incident: Incident) -> None:
    _atomic_write(path, json.dumps(asdict(incident), ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _same_incident(left: Incident, right: Incident) -> bool:
    return (
        left.incident_id == right.incident_id
        and left.fingerprint == right.fingerprint
        and left.project_id == right.project_id
        and left.probe_id == right.probe_id
    )


def apply_observation(state_root: Path, observation: Observation) -> tuple[str, Incident | None]:
    open_path = _open_path(state_root, observation.fingerprint)
    current = _load(open_path)
    now = utc_now()

    if observation.ok:
        if current is None:
            return "healthy", None
        current.status = "resolved"
        current.updated_at = now
        current.resolved_at = now
        current.last_detail = observation.detail
        current.last_latency_ms = observation.latency_ms
        current.last_observation_at = observation.checked_at
        resolved_path = _resolved_path(state_root, current.incident_id)
        existing = _load(resolved_path)
        if existing is not None:
            if not _same_incident(existing, current):
                raise RuntimeError(f"resolved incident collision: {current.incident_id}")
            open_path.unlink(missing_ok=True)
            return "resolved", existing
        _save(resolved_path, current)
        open_path.unlink(missing_ok=True)
        return "resolved", current

    if current is None:
        opened_at = now
        current = Incident(
            version=INCIDENT_VERSION,
            incident_id=incident_id_for(observation, opened_at),
            fingerprint=observation.fingerprint,
            project_id=observation.project_id,
            probe_id=observation.probe_id,
            severity=observation.severity,
            status="open",
            opened_at=opened_at,
            updated_at=now,
            resolved_at="",
            failure_count=1,
            last_detail=observation.detail,
            last_latency_ms=observation.latency_ms,
            last_observation_at=observation.checked_at,
        )
        resolved_path = _resolved_path(state_root, current.incident_id)
        if resolved_path.exists():
            raise RuntimeError(f"new incident id collides with history: {current.incident_id}")
        _save(open_path, current)
        return "opened", current

    current.updated_at = now
    current.failure_count += 1
    current.severity = observation.severity
    current.last_detail = observation.detail
    current.last_latency_ms = observation.latency_ms
    current.last_observation_at = observation.checked_at
    _save(open_path, current)
    return "updated", current


def reconcile(state_root: Path, observations: list[Observation]) -> list[tuple[str, Incident | None]]:
    return [apply_observation(state_root, observation) for observation in observations]


def summary(state_root: Path) -> dict:
    open_dir = state_root / "incidents" / "open"
    resolved_dir = state_root / "incidents" / "resolved"
    open_items = []
    for path in sorted(open_dir.glob("*.json")) if open_dir.is_dir() else []:
        incident = _load(path)
        if incident is not None:
            open_items.append(asdict(incident))
    resolved_count = 0
    if resolved_dir.is_dir():
        for path in sorted(resolved_dir.glob("INC-*.json")):
            if _load(path) is not None:
                resolved_count += 1
    return {
        "version": INCIDENT_VERSION,
        "generated_at": utc_now(),
        "open_count": len(open_items),
        "resolved_count": resolved_count,
        "open_incidents": open_items,
    }


def write_summary(state_root: Path) -> Path:
    destination = state_root / "incidents" / "summary.json"
    _atomic_write(destination, json.dumps(summary(state_root), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--project")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        observations = monitor_projects(args.root, args.project)
        write_snapshot(args.state_root, observations)
        transitions = reconcile(args.state_root, observations)
        write_summary(args.state_root)
    except (ProjectPolicyError, MonitoringConfigError, RuntimeError) as exc:
        print(f"INCIDENT_ENGINE_ERROR: {exc}")
        return 2

    if args.json:
        payload = [
            {"transition": transition, "incident": asdict(incident) if incident else None}
            for transition, incident in transitions
        ]
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        for (transition, incident), observation in zip(transitions, observations):
            incident_id = incident.incident_id if incident else "-"
            print(
                f"{transition.upper()} project={observation.project_id} probe={observation.probe_id} "
                f"incident={incident_id}"
            )
    return 1 if any(not item.ok for item in observations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
