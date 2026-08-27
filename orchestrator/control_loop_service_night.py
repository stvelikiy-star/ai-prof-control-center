#!/usr/bin/env python3
"""Night-safe Control Center entrypoint.

This wrapper changes exactly one child-stage binding: the AI PROF self-
maintenance commit gate is upgraded to the terminal reconciler V2. KOL,
AK BERMET, normal Stage 01 stages, dedicated Telegram service identity, and all
other Control Center behavior remain owned by control_loop_service.py.
"""
from __future__ import annotations

from pathlib import Path

try:  # Package import under tests.
    from orchestrator import control_loop_service as base
except ImportError:  # Direct script execution from orchestrator/.
    import control_loop_service as base  # type: ignore[no-redef]


def _commands_with_night_safe_ai_prof_gate(
    root: Path,
    runtime: Path,
) -> list[tuple[str, list[str]]]:
    established = base._commands_with_publishers(root, runtime)
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
