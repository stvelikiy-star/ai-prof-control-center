#!/usr/bin/env python3
"""Deterministic health snapshot for Repair Team shadow queues.

This module reads only AI PROF state and writes one bounded health JSON record.
It does not call an AI model, inspect secrets, mutate target projects, create
repair tasks, or change authority. Historical blocked records do not keep the
health permanently degraded: only blocked records bound to currently open
incidents count as active blockers.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from incident_engine import DEFAULT_STATE_ROOT, summary as incident_summary

HEALTH_VERSION = 1
MAX_PENDING_AGE_SECONDS = 1800.0
MAX_HEALTH_AGE_SECONDS = 1500.0
MAX_REASONS = 16
HEALTH_RELATIVE_PATH = Path("shadow_queue_health/latest.json")


class ShadowQueueHealthError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    parent = path.parent
    if parent.is_symlink():
        raise ShadowQueueHealthError(f"health directory symlink rejected: {parent}")
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or path.is_symlink():
        raise ShadowQueueHealthError("health state symlink rejected")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_files(directory: Path, pattern: str = "INC-*.json") -> list[Path]:
    if directory.is_symlink():
        raise ShadowQueueHealthError(f"state directory symlink rejected: {directory}")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ShadowQueueHealthError(f"state path is not a directory: {directory}")
    files: list[Path] = []
    for path in sorted(directory.glob(pattern)):
        if path.is_symlink() or not path.is_file():
            raise ShadowQueueHealthError(f"state entry is not a regular file: {path}")
        files.append(path)
    return files


def _oldest_age_seconds(paths: list[Path], now_epoch: float) -> float | None:
    if not paths:
        return None
    ages = []
    for path in paths:
        try:
            modified = path.stat().st_mtime
        except OSError as exc:
            raise ShadowQueueHealthError(f"cannot stat queue state: {type(exc).__name__}") from exc
        ages.append(max(0.0, now_epoch - modified))
    return max(ages)


def _open_incident_ids(state_root: Path) -> set[str]:
    open_dir = state_root / "incidents" / "open"
    if open_dir.is_symlink():
        raise ShadowQueueHealthError("open incident directory symlink rejected")
    data = incident_summary(state_root)
    result: set[str] = set()
    for item in data.get("open_incidents", []):
        if not isinstance(item, dict):
            raise ShadowQueueHealthError("invalid open incident summary item")
        incident_id = item.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id:
            raise ShadowQueueHealthError("invalid open incident id")
        result.add(incident_id)
    return result


def build_snapshot(state_root: Path, *, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    now_epoch = current.timestamp()

    open_ids = _open_incident_ids(state_root)
    diagnosis_pending = _json_files(state_root / "diagnosis" / "pending")
    diagnosis_blocked = [
        path
        for path in _json_files(state_root / "diagnosis" / "blocked")
        if path.stem in open_ids
    ]
    diagnosis_results = _json_files(state_root / "diagnosis" / "results")
    bridge_tasks = {path.stem for path in _json_files(state_root / "repair_bridge" / "tasks")}
    bridge_blocked_files = _json_files(state_root / "repair_bridge" / "blocked")
    bridge_blocked = [path for path in bridge_blocked_files if path.stem in open_ids]
    bridged_or_blocked = bridge_tasks | {path.stem for path in bridge_blocked_files}
    bridge_unprocessed = [path for path in diagnosis_results if path.stem not in bridged_or_blocked]

    diagnosis_oldest = _oldest_age_seconds(diagnosis_pending, now_epoch)
    bridge_oldest = _oldest_age_seconds(bridge_unprocessed, now_epoch)
    reasons: list[str] = []
    if diagnosis_blocked:
        reasons.append("open_diagnosis_blocked")
    if bridge_blocked:
        reasons.append("open_repair_bridge_blocked")
    if diagnosis_oldest is not None and diagnosis_oldest > MAX_PENDING_AGE_SECONDS:
        reasons.append("diagnosis_pending_too_old")
    if bridge_oldest is not None and bridge_oldest > MAX_PENDING_AGE_SECONDS:
        reasons.append("repair_bridge_pending_too_old")

    ok = not reasons
    return {
        "version": HEALTH_VERSION,
        "timestamp": current.isoformat(),
        "ok": ok,
        "state": "healthy" if ok else "degraded",
        "thresholds": {
            "max_pending_age_seconds": MAX_PENDING_AGE_SECONDS,
            "max_health_age_seconds": MAX_HEALTH_AGE_SECONDS,
        },
        "diagnosis": {
            "pending_count": len(diagnosis_pending),
            "oldest_pending_age_seconds": diagnosis_oldest,
            "blocked_open_count": len(diagnosis_blocked),
        },
        "bridge": {
            "unprocessed_count": len(bridge_unprocessed),
            "oldest_unprocessed_age_seconds": bridge_oldest,
            "blocked_open_count": len(bridge_blocked),
        },
        "reasons": reasons[:MAX_REASONS],
    }


def write_snapshot(state_root: Path, snapshot: dict) -> Path:
    destination = state_root / HEALTH_RELATIVE_PATH
    _atomic_write(
        destination,
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return destination


def write_current_health(state_root: Path) -> Path:
    try:
        snapshot = build_snapshot(state_root)
    except Exception as exc:
        snapshot = {
            "version": HEALTH_VERSION,
            "timestamp": utc_now(),
            "ok": False,
            "state": "degraded",
            "thresholds": {
                "max_pending_age_seconds": MAX_PENDING_AGE_SECONDS,
                "max_health_age_seconds": MAX_HEALTH_AGE_SECONDS,
            },
            "diagnosis": {},
            "bridge": {},
            "reasons": [f"health_collection_error:{type(exc).__name__}"],
        }
    return write_snapshot(state_root, snapshot)


def read_health(state_root: Path, *, now: datetime | None = None) -> tuple[bool, str]:
    path = state_root / HEALTH_RELATIVE_PATH
    if path.is_symlink():
        return False, "shadow health symlink rejected"
    if not path.is_file():
        return False, "shadow health missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"shadow health invalid:{type(exc).__name__}"
    required = {"version", "timestamp", "ok", "state", "thresholds", "diagnosis", "bridge", "reasons"}
    if not isinstance(payload, dict) or set(payload) != required or payload.get("version") != HEALTH_VERSION:
        return False, "shadow health schema invalid"
    if not isinstance(payload.get("ok"), bool):
        return False, "shadow health ok flag invalid"
    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, str):
        return False, "shadow health timestamp missing"
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = max(0.0, (current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return False, "shadow health timestamp invalid"
    state = str(payload.get("state", "unknown"))[:80]
    fresh = age <= MAX_HEALTH_AGE_SECONDS
    ok = bool(payload["ok"]) and fresh
    return ok, (
        f"age_seconds={age:.1f} max_age_seconds={MAX_HEALTH_AGE_SECONDS:.1f} "
        f"state={state} reported_ok={payload['ok']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        path = write_current_health(args.state_root)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        message = f"SHADOW_QUEUE_HEALTH_ERROR:{type(exc).__name__}"
        if args.json:
            print(json.dumps({"status": "blocked", "error": message}, sort_keys=True))
        else:
            print(message)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"SHADOW_QUEUE_HEALTH state={payload['state']} ok={payload['ok']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
