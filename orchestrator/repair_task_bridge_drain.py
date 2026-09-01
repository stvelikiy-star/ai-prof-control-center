#!/usr/bin/env python3
"""Bounded sequential drain for validated Repair Team diagnosis results.

The underlying bridge keeps all authority, policy, scope and task validation.
This wrapper only repeats its existing ``process_once`` primitive so already
validated diagnosis results do not wait for separate two-minute service cycles.
No repair is executed here and no parallel workers are created.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import repair_task_bridge as base

MAX_RESULTS_PER_RUN = 16


class BridgeDrainError(ValueError):
    pass


def drain(
    root: Path,
    state_root: Path,
    *,
    max_results: int = MAX_RESULTS_PER_RUN,
) -> list[base.BridgeResult]:
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not (1 <= max_results <= MAX_RESULTS_PER_RUN):
        raise BridgeDrainError("max_results outside bounded bridge drain contract")

    results: list[base.BridgeResult] = []
    while len(results) < max_results:
        result = base.process_once(root, state_root)
        if result is None:
            break
        results.append(result)
        # A blocked bridge result can indicate a shared registry/policy/state
        # problem. Stop to avoid cascading the same failure across the queue.
        if result.status in {"blocked", "already_blocked"}:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=base.DEFAULT_ROOT)
    parser.add_argument("--state-root", type=Path, default=base.DEFAULT_STATE_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        results = drain(args.root, args.state_root)
    except Exception as exc:
        message = base._safe_one_line(str(exc), 1000)
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
            print(f"{result.status.upper()} incident={result.incident_id} task={result.task_id} path={result.path}")
    return 1 if any(result.status in {"blocked", "already_blocked"} for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
