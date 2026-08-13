#!/usr/bin/env python3
"""Systemd entrypoint for AI PROF Control Center without embedded Telegram.

Mobile Control runs Telegram as its own dedicated service. This runtime adapter
also inserts the trusted KÖL approved-task publisher before and after the normal
Stage 01 pipeline. The publisher is a fail-closed repository-state gate: it can
publish only an already Stage-01C-approved KÖL work branch as a private PR; it
cannot merge, deploy, access secrets, or mutate a database.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import control_loop

_ORIGINAL_CHILD_COMMANDS = control_loop.child_commands


def _dedicated_telegram_service_only(
    _paths: control_loop.ControlPaths,
    stop_event: threading.Event,
    **_kwargs,
) -> None:
    """Do not spawn legacy Telegram; wait until Control Center shuts down."""
    stop_event.wait()


def _publisher_argv(root: Path, runtime: Path) -> list[str]:
    return [
        sys.executable,
        str(root / "orchestrator/approved_task_publisher_gate.py"),
        "--root",
        str(root),
        "--state-root",
        str(runtime),
        "--once",
    ]


def _commands_with_publisher(
    root: Path,
    runtime: Path,
) -> list[tuple[str, list[str]]]:
    """Publish prior PASS work first, then immediately finalize a new PASS."""
    normal = _ORIGINAL_CHILD_COMMANDS(root, runtime)
    publisher = _publisher_argv(root, runtime)
    return [
        ("approved_publisher_pre", publisher),
        *normal,
        ("approved_publisher_post", publisher),
    ]


def main() -> int:
    control_loop.supervise_telegram_bridge = _dedicated_telegram_service_only
    control_loop.child_commands = _commands_with_publisher
    return control_loop.main()


if __name__ == "__main__":
    raise SystemExit(main())
