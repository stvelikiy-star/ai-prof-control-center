"""Persistent flap suppression for Repair Team incident reconciliation.

The monitor runs once per minute. A transient observation must therefore repeat
on two consecutive cycles before an incident opens, and a recovery must repeat
on two consecutive cycles before an open incident resolves. This module only
stores evidence counters under the Control Center state root; it has no repair,
merge, deployment, shell, or project-mutation authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

HYSTERESIS_VERSION = 1
FAILURES_TO_OPEN = 2
SUCCESSES_TO_RESOLVE = 2


class HysteresisStateError(RuntimeError):
    pass


@dataclass
class HysteresisState:
    version: int
    fingerprint: str
    consecutive_failures: int
    consecutive_successes: int
    last_observation_at: str


def _safe_key(fingerprint: str) -> str:
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


def state_path(state_root: Path, fingerprint: str) -> Path:
    return state_root / "incidents" / "hysteresis" / f"{_safe_key(fingerprint)}.json"


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HysteresisStateError(f"invalid observation timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise HysteresisStateError("observation timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise HysteresisStateError(f"hysteresis state directory symlink rejected: {path.parent}")
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


def load(state_root: Path, fingerprint: str) -> HysteresisState:
    path = state_path(state_root, fingerprint)
    if path.is_symlink():
        raise HysteresisStateError(f"hysteresis state symlink rejected: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HysteresisState(HYSTERESIS_VERSION, fingerprint, 0, 0, "")
    except (OSError, ValueError) as exc:
        raise HysteresisStateError(f"invalid hysteresis state {path}: {exc}") from exc
    required = {
        "version",
        "fingerprint",
        "consecutive_failures",
        "consecutive_successes",
        "last_observation_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise HysteresisStateError(f"invalid hysteresis schema: {path}")
    if payload.get("version") != HYSTERESIS_VERSION or payload.get("fingerprint") != fingerprint:
        raise HysteresisStateError(f"hysteresis identity mismatch: {path}")
    failures = payload.get("consecutive_failures")
    successes = payload.get("consecutive_successes")
    observed = payload.get("last_observation_at")
    if (
        isinstance(failures, bool)
        or not isinstance(failures, int)
        or failures < 0
        or failures > FAILURES_TO_OPEN
        or isinstance(successes, bool)
        or not isinstance(successes, int)
        or successes < 0
        or successes > SUCCESSES_TO_RESOLVE
        or not isinstance(observed, str)
    ):
        raise HysteresisStateError(f"invalid hysteresis counters: {path}")
    if failures and successes:
        raise HysteresisStateError(f"conflicting hysteresis counters: {path}")
    if observed:
        _timestamp(observed)
    return HysteresisState(**payload)


def record(state_root: Path, fingerprint: str, ok: bool, checked_at: str) -> HysteresisState:
    current_timestamp = _timestamp(checked_at)
    state = load(state_root, fingerprint)
    if state.last_observation_at and current_timestamp <= _timestamp(state.last_observation_at):
        raise HysteresisStateError("replayed or out-of-order observation rejected")
    if ok:
        state.consecutive_successes = min(
            SUCCESSES_TO_RESOLVE, state.consecutive_successes + 1
        )
        state.consecutive_failures = 0
    else:
        state.consecutive_failures = min(
            FAILURES_TO_OPEN, state.consecutive_failures + 1
        )
        state.consecutive_successes = 0
    state.last_observation_at = checked_at
    _atomic_write(
        state_path(state_root, fingerprint),
        json.dumps(asdict(state), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return state


def clear(state_root: Path, fingerprint: str) -> None:
    path = state_path(state_root, fingerprint)
    if path.is_symlink():
        raise HysteresisStateError(f"hysteresis state symlink rejected: {path}")
    path.unlink(missing_ok=True)
