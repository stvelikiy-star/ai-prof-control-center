#!/usr/bin/env python3
"""Canary incident entrypoint with persistent two-cycle flap suppression.

The canonical monitor timer runs once per minute. This wrapper preserves the
existing deterministic incident engine and changes only reconciliation: two
consecutive failures are required to open an incident and two consecutive
successes are required to resolve one. It adds no repair, merge, deployment,
shell, or project-mutation authority.
"""
from __future__ import annotations

try:  # Package import under unit tests.
    from orchestrator import incident_engine as base
    from orchestrator import incident_hysteresis as hysteresis
except ImportError:  # Direct script execution from orchestrator/.
    import incident_engine as base  # type: ignore[no-redef]
    import incident_hysteresis as hysteresis  # type: ignore[no-redef]


def reconcile(state_root, observations):
    results = []
    for observation in observations:
        current = base._load(base._open_path(state_root, observation.fingerprint))
        streak = hysteresis.record(
            state_root,
            observation.fingerprint,
            observation.ok,
            observation.checked_at,
        )

        if observation.ok:
            if current is None:
                hysteresis.clear(state_root, observation.fingerprint)
                results.append(("healthy", None))
                continue
            if streak.consecutive_successes < hysteresis.SUCCESSES_TO_RESOLVE:
                results.append(("pending_recovery", current))
                continue
            result = base.apply_observation(state_root, observation)
            hysteresis.clear(state_root, observation.fingerprint)
            results.append(result)
            continue

        if current is None and streak.consecutive_failures < hysteresis.FAILURES_TO_OPEN:
            results.append(("pending_failure", None))
            continue
        results.append(base.apply_observation(state_root, observation))
    return results


def main() -> int:
    original = base.reconcile
    try:
        base.reconcile = reconcile
        return base.main()
    finally:
        base.reconcile = original


if __name__ == "__main__":
    raise SystemExit(main())
