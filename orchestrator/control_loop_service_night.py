#!/usr/bin/env python3
"""Night-safe Control Center entrypoint.

This wrapper adds bounded reliability changes while preserving all existing
project publishers and the dedicated Telegram runtime identity:

1. AI PROF self-maintenance uses the terminal commit reconciler V2.
2. While an AI PROF maintenance code task is already active, in review,
   pending Codex audit, or approved but not yet terminalized, a new generic
   Stage 01A code task is not started. This prevents another task from stealing
   the single maintenance checkout before the current task reaches its terminal
   state.
3. Heartbeat writes always refresh the supervisor PID after loading historical
   heartbeat fields, so a service restart cannot keep reporting a stale PID.
4. The operations stage uses the night wrapper that upgrades only the legacy
   plain ``python -m unittest`` health command to explicit full discovery.
5. While a KÖL V4 task is in flight, cross-project mutators are isolated: AK
   BERMET publisher stages and the shared campaign tick are skipped. Isolation
   is latched for the whole live cycle and clears only after the runtime is
   observably paused, preventing a terminal-state race from touching another
   project before the external single-flight monitor can restore pause.
6. The isolation latch is also armed from the first non-paused live heartbeat,
   so a service that was started while paused cannot lose KÖL isolation merely
   because its command composition happened before the live transition.

No push, merge, deploy, database, secret, or destructive authority is added.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

try:  # Package import under tests.
    from orchestrator import control_loop_service as base
except ImportError:  # Direct script execution from orchestrator/.
    import control_loop_service as base  # type: ignore[no-redef]

MAINTENANCE_PROJECT_PATH = "/home/agent/projects/ai-prof-control-center-maintenance"
KOL_PROJECT_PATH = "/home/agent/Загрузки/kol-travel-platform"
IN_FLIGHT_QUEUES = ("active", "review", "pending_codex", "approved")
KOL_V4_QUEUES = ("pending", "active", "review", "pending_codex", "approved")
AK_BERMET_PUBLISHER_STAGES = {
    "ak_bermet_approved_publisher_pre",
    "ak_bermet_approved_publisher_post",
}
MAX_TASK_BYTES = 1024 * 1024
KOL_V4_ISOLATION_MARKER = "kol-v4-isolation"
_ORIGINAL_CAMPAIGN_TICK_ALL = base.control_loop.campaign_runner.tick_all


def _field(text: str, name: str) -> str | None:
    prefix = f"{name}:"
    values = [
        line[len(prefix):].strip()
        for line in text.splitlines()
        if line.startswith(prefix)
    ]
    if len(values) != 1 or not values[0]:
        return None
    return values[0]


def _maintenance_code_task_in_flight(runtime: Path) -> bool:
    """Fail-safe single-flight check for the shared maintenance checkout."""
    queue_root = runtime / "queue"
    for queue_name in IN_FLIGHT_QUEUES:
        queue = queue_root / queue_name
        if not queue.exists():
            continue
        if queue.is_symlink() or not queue.is_dir():
            return True
        for path in sorted(queue.glob("*.md")):
            try:
                if path.is_symlink() or not path.is_file():
                    return True
                if path.stat().st_size > MAX_TASK_BYTES:
                    return True
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return True
            if (
                _field(text, "Execution-Mode") == "code"
                and _field(text, "Project-Path") == MAINTENANCE_PROJECT_PATH
            ):
                return True
    return False


def _kol_v4_task_in_flight(runtime: Path) -> bool:
    """Detect one bounded KÖL V4 task and fail safe on malformed queue state."""
    queue_root = runtime / "queue"
    for queue_name in KOL_V4_QUEUES:
        queue = queue_root / queue_name
        if not queue.exists():
            continue
        if queue.is_symlink() or not queue.is_dir():
            return True
        for path in sorted(queue.glob("*.md")):
            try:
                if path.is_symlink() or not path.is_file():
                    return True
                if path.stat().st_size > MAX_TASK_BYTES:
                    return True
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return True
            if (
                _field(text, "Project-Path") == KOL_PROJECT_PATH
                and _field(text, "Publication-Contract-Version") == "4"
            ):
                return True
    return False


def _kol_v4_isolation_marker(runtime: Path) -> Path:
    return runtime / "run" / KOL_V4_ISOLATION_MARKER


def _kol_v4_isolation_active(runtime: Path) -> bool:
    marker = _kol_v4_isolation_marker(runtime)
    if marker.exists():
        return True
    return _kol_v4_task_in_flight(runtime)


def _arm_kol_v4_isolation(runtime: Path) -> bool:
    if not _kol_v4_task_in_flight(runtime):
        return _kol_v4_isolation_active(runtime)
    marker = _kol_v4_isolation_marker(runtime)
    marker.parent.mkdir(parents=True, exist_ok=True)
    base.control_loop.atomic_write(marker, "active\n")
    return True


def _clear_kol_v4_isolation(runtime: Path) -> None:
    _kol_v4_isolation_marker(runtime).unlink(missing_ok=True)


def _night_campaign_tick_all(root: Path, state_root: Path) -> int:
    """Do not mutate campaign projects while a KÖL V4 live cycle is isolated."""
    runtime = Path(state_root)
    if _kol_v4_isolation_active(runtime):
        return 0
    return _ORIGINAL_CAMPAIGN_TICK_ALL(root, state_root)


def _night_write_heartbeat(paths, **updates) -> None:
    """Preserve heartbeat state, refresh PID, and enforce KÖL live isolation."""
    control = base.control_loop
    state = {
        "timestamp": control.utc_now(),
        "pid": os.getpid(),
        "state": "idle",
        "stage": None,
        "last_result": None,
        "consecutive_failures": 0,
    }
    try:
        loaded = json.loads(paths.heartbeat.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state.update(loaded)
    except (OSError, ValueError):
        pass
    state.update(updates)
    state["pid"] = os.getpid()
    state["timestamp"] = control.utc_now()
    control.atomic_write(
        paths.heartbeat,
        json.dumps(state, sort_keys=True) + "\n",
    )

    state_path = getattr(paths, "state", None)
    if isinstance(state_path, Path):
        runtime = state_path.parent
        if state.get("state") == "paused":
            _clear_kol_v4_isolation(runtime)
        elif _kol_v4_task_in_flight(runtime):
            _arm_kol_v4_isolation(runtime)


def _upgrade_operations_binding(
    root: Path,
    commands: list[tuple[str, list[str]]],
) -> list[tuple[str, list[str]]]:
    """Replace only the exact legacy operations script with night wrapper."""
    legacy_path = str(root / "orchestrator/operations_runner.py")
    night_path = str(root / "orchestrator/operations_runner_night.py")
    upgraded: list[tuple[str, list[str]]] = []
    for stage, argv in commands:
        if stage == "operations":
            argv = [night_path if item == legacy_path else item for item in argv]
        upgraded.append((stage, argv))
    return upgraded


def _commands_with_night_safe_ai_prof_gate(
    root: Path,
    runtime: Path,
) -> list[tuple[str, list[str]]]:
    isolated = _arm_kol_v4_isolation(runtime)
    established = _upgrade_operations_binding(
        root,
        base._commands_with_publishers(root, runtime),
    )
    if _maintenance_code_task_in_flight(runtime):
        established = [
            (stage, argv)
            for stage, argv in established
            if stage != "stage_01a"
        ]
    if isolated:
        established = [
            (stage, argv)
            for stage, argv in established
            if stage not in AK_BERMET_PUBLISHER_STAGES
        ]

    ai_prof_publisher_gate = base._publisher_argv(
        root,
        runtime,
        "ai_prof_approved_task_publisher_gate_v2.py",
    )
    pre_end = 1 if isolated else 2
    post_start = len(established) - pre_end
    return [
        *established[:pre_end],
        ("ai_prof_approved_publisher_pre", ai_prof_publisher_gate),
        *established[pre_end:post_start],
        *established[post_start:],
        ("ai_prof_approved_publisher_post", ai_prof_publisher_gate),
    ]


def main() -> int:
    base.control_loop.write_heartbeat = _night_write_heartbeat
    base.control_loop.campaign_runner.tick_all = _night_campaign_tick_all
    base._commands_with_ai_prof_publisher_gate = _commands_with_night_safe_ai_prof_gate
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
