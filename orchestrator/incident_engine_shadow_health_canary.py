#!/usr/bin/env python3
"""Monitor canary that appends fixed Repair Team shadow-queue health evidence.

Regular project probes still come from the existing monitoring engine. This
wrapper adds exactly one built-in Control Center observation from the trusted
AI PROF state path and then reuses the existing two-cycle hysteresis reconcile.
It does not add arbitrary probe configuration or any repair authority.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

try:
    from orchestrator import incident_engine_canary as canary
    from orchestrator import shadow_queue_health as health
except ImportError:
    import incident_engine_canary as canary  # type: ignore[no-redef]
    import shadow_queue_health as health  # type: ignore[no-redef]

base = canary.base
CONTROL_CENTER_PROJECT_ID = "ai-prof-control-center"
SHADOW_HEALTH_PROBE_ID = "shadow-queue-health"


def shadow_health_observation(state_root):
    ok, detail = health.read_health(state_root)
    return base.Observation(
        project_id=CONTROL_CENTER_PROJECT_ID,
        probe_id=SHADOW_HEALTH_PROBE_ID,
        kind="shadow_health_json",
        severity="warning",
        ok=ok,
        checked_at=base.utc_now(),
        latency_ms=0,
        detail=detail,
        fingerprint=f"{CONTROL_CENTER_PROJECT_ID}:{SHADOW_HEALTH_PROBE_ID}",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=base.DEFAULT_ROOT)
    parser.add_argument("--state-root", type=Path, default=base.DEFAULT_STATE_ROOT)
    parser.add_argument("--project")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        observations = base.monitor_projects(args.root, args.project)
        if args.project in {None, CONTROL_CENTER_PROJECT_ID}:
            observations.append(shadow_health_observation(args.state_root))
        base.write_snapshot(args.state_root, observations)
        transitions = canary.reconcile(args.state_root, observations)
        base.write_summary(args.state_root)
    except (base.ProjectPolicyError, base.MonitoringConfigError, RuntimeError) as exc:
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
