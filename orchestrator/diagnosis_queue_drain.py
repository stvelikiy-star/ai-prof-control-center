#!/usr/bin/env python3
"""Bounded sequential drain for Repair Team shadow diagnosis packets.

The base incident diagnosis runner remains the authority for packet validation,
Codex sandboxing, policy classification and result persistence. This wrapper
only calls its existing ``process_once`` primitive repeatedly while there is
sufficient wall-clock budget for another full Codex timeout plus a safety
margin. It never runs diagnoses in parallel and stops immediately on a blocked
result so one infrastructure failure cannot mass-block the remaining queue.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

import incident_diagnosis_runner as base

MAX_PACKETS_PER_RUN = 4
SERVICE_BUDGET_SECONDS = 1100
SAFETY_MARGIN_SECONDS = 60
MIN_REMAINING_FOR_NEW_CODEX = base.cr.CODEX_TIMEOUT_SECONDS + SAFETY_MARGIN_SECONDS


class DiagnosisDrainError(ValueError):
    pass


def _validate_limits(max_packets: int, budget_seconds: float) -> None:
    if isinstance(max_packets, bool) or not isinstance(max_packets, int) or not (1 <= max_packets <= MAX_PACKETS_PER_RUN):
        raise DiagnosisDrainError("max_packets outside bounded drain contract")
    if isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float)):
        raise DiagnosisDrainError("budget_seconds must be numeric")
    if budget_seconds < MIN_REMAINING_FOR_NEW_CODEX or budget_seconds > SERVICE_BUDGET_SECONDS:
        raise DiagnosisDrainError("budget_seconds outside bounded drain contract")


def drain(
    root: Path,
    state_root: Path,
    *,
    codex_cli: Path = base.DEFAULT_CODEX_CLI,
    invoke_fn=None,
    max_packets: int = MAX_PACKETS_PER_RUN,
    budget_seconds: float = SERVICE_BUDGET_SECONDS,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> list[base.ProcessResult]:
    _validate_limits(max_packets, budget_seconds)
    started = monotonic_fn()
    results: list[base.ProcessResult] = []

    while len(results) < max_packets:
        if results:
            elapsed = max(0.0, monotonic_fn() - started)
            remaining = budget_seconds - elapsed
            if remaining < MIN_REMAINING_FOR_NEW_CODEX:
                break

        result = base.process_once(
            root,
            state_root,
            codex_cli=codex_cli,
            invoke_fn=invoke_fn,
        )
        if result is None:
            break
        results.append(result)

        # A blocked result can represent a shared Codex/auth/filesystem outage.
        # Stop rather than cascading the same infrastructure failure through
        # every pending incident in one service activation.
        if result.status in {"blocked", "already_blocked"}:
            break

    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=base.DEFAULT_ROOT)
    parser.add_argument("--state-root", type=Path, default=base.DEFAULT_STATE_ROOT)
    parser.add_argument("--codex-cli", type=Path, default=base.DEFAULT_CODEX_CLI)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        results = drain(args.root, args.state_root, codex_cli=args.codex_cli)
    except Exception as exc:
        message = base._redact(str(exc))
        if args.json:
            print(json.dumps({"status": "blocked", "error": message}, ensure_ascii=False, sort_keys=True))
        else:
            print(f"BLOCKED {message}")
        return 1

    payload = [result.__dict__ for result in results]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif not results:
        print("IDLE")
    else:
        for result in results:
            print(f"{result.status.upper()} incident={result.incident_id} result={result.result_path}")
    return 1 if any(result.status in {"blocked", "already_blocked"} for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
