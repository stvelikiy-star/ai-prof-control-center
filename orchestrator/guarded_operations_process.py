"""Guarded canonical operations queue processor for Repair Team authority."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from incident_operation_execution_gate import (
    IncidentOperationGateError,
    validate_incident_operation_authority,
)


def process_one(base: ModuleType, paths) -> int:
    """Run the legacy operations lifecycle with execution-time incident authority checks."""
    task: Path | None = None
    promoted_profile: str | None = None
    for candidate in sorted(paths.pending.glob("*.md")):
        try:
            data, _ = base.orch.parse_task(candidate)
        except Exception:
            continue
        profile = base.legacy_runtime_profile(data)
        if data["Execution-Mode"] == "operations" or profile:
            task = candidate
            promoted_profile = profile
            break
    if task is None:
        print("QUEUE_EMPTY")
        return 0

    active = base.orch.safe_move(task, paths.active)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = paths.logs / f"{active.stem}-operations-{timestamp}.log"
    try:
        data, _task_text = base.orch.parse_task(active)
        state_root = paths.pending.parent.parent
        try:
            validate_incident_operation_authority(paths.root, state_root, data)
        except IncidentOperationGateError as exc:
            raise base.OperationBlocked(str(exc)) from exc

        profile_key = promoted_profile or data["Operation-Profile"]
        try:
            profile = base.resolve_profile(profile_key)
        except ValueError as exc:
            raise base.OperationBlocked(str(exc)) from exc
        outcome = base.execute_profile(profile, data["Project-Path"])
        summary = (
            "PASS\n"
            f"task_id={data['Task-ID']}\n"
            f"profile={profile.key}\n"
            f"outcome={outcome}\n"
            f"legacy_runtime_promotion={'true' if promoted_profile else 'false'}\n"
            "incident_operation_execution_gate=true\n"
            "task_text_executed=false\nshell=false\nworking_tree=preserved\n"
        )
        log_path.write_text(base.redact(summary), encoding="utf-8")
        base.orch.safe_move(active, paths.completed)
        print("PASS")
        return 0
    except base.OperationBlocked as exc:
        safe = base.redact(str(exc))
        base._terminal_reason(active, "Blocked-Reason", safe)
        log_path.write_text(base.redact(f"BLOCKED\n{safe}\n"), encoding="utf-8")
        if active.exists():
            base.orch.safe_move(active, paths.blocked)
        print("BLOCKED", file=sys.stderr)
        return 1
    except base.OperationFailed as exc:
        safe = base.redact(str(exc))
        base._terminal_reason(active, "Failure-Reason", safe)
        log_path.write_text(base.redact(f"FAILED\n{safe}\n"), encoding="utf-8")
        if active.exists():
            base.orch.safe_move(active, paths.failed)
        print("FAILED", file=sys.stderr)
        return 1
    except Exception as exc:
        reason = f"UNEXPECTED_{type(exc).__name__}"
        base._terminal_reason(active, "Failure-Reason", reason)
        log_path.write_text(
            base.redact(f"FAILED\n{type(exc).__name__}: {exc}\n"), encoding="utf-8"
        )
        if active.exists():
            base.orch.safe_move(active, paths.failed)
        print("FAILED", file=sys.stderr)
        return 1
