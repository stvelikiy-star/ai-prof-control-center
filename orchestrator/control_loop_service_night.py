#!/usr/bin/env python3
"""Night-safe Control Center entrypoint.

This wrapper adds two bounded reliability changes while preserving all existing
project publishers and the dedicated Telegram runtime identity:

1. AI PROF self-maintenance uses the terminal commit reconciler V2.
2. While an AI PROF maintenance code task is already active, in review, or
   pending Codex audit, a new generic Stage 01A code task is not started. This
   prevents another task from stealing the single maintenance checkout before
   the current task reaches its terminal state.

No push, merge, deploy, database, secret, or destructive authority is added.
"""
from __future__ import annotations

from pathlib import Path

try:  # Package import under tests.
    from orchestrator import control_loop_service as base
except ImportError:  # Direct script execution from orchestrator/.
    import control_loop_service as base  # type: ignore[no-redef]

MAINTENANCE_PROJECT_PATH = "/home/agent/projects/ai-prof-control-center-maintenance"
IN_FLIGHT_QUEUES = ("active", "review", "pending_codex")
MAX_TASK_BYTES = 1024 * 1024


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


def _commands_with_night_safe_ai_prof_gate(
    root: Path,
    runtime: Path,
) -> list[tuple[str, list[str]]]:
    established = base._commands_with_publishers(root, runtime)
    if _maintenance_code_task_in_flight(runtime):
        established = [
            (stage, argv)
            for stage, argv in established
            if stage != "stage_01a"
        ]

    ai_prof_publisher_gate = base._publisher_argv(
        root,
        runtime,
        "ai_prof_approved_task_publisher_gate_v2.py",
    )
    pre_end = 2
    post_start = len(established) - 2
    return [
        *established[:pre_end],
        ("ai_prof_approved_publisher_pre", ai_prof_publisher_gate),
        *established[pre_end:post_start],
        *established[post_start:],
        ("ai_prof_approved_publisher_post", ai_prof_publisher_gate),
    ]


def main() -> int:
    base._commands_with_ai_prof_publisher_gate = _commands_with_night_safe_ai_prof_gate
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
