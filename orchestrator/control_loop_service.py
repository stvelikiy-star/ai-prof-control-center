#!/usr/bin/env python3
"""Systemd entrypoint for AI PROF Control Center without embedded Telegram.

Mobile Control runs Telegram as its own dedicated service. This runtime adapter
inserts trusted post-audit publishers before and after the normal Stage 01
pipeline. Each publisher is a fail-closed repository-state gate and can act
only on a Stage-01C-approved task for its exact pinned project. Publishers can
commit the approved scope, push that work branch and open a PR; they cannot
merge, deploy, access secrets, or mutate a database.
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


def _publisher_argv(root: Path, runtime: Path, script_name: str) -> list[str]:
    return [
        sys.executable,
        str(root / f"orchestrator/{script_name}"),
        "--root",
        str(root),
        "--state-root",
        str(runtime),
        "--once",
    ]


def _commands_with_publishers(
    root: Path,
    runtime: Path,
) -> list[tuple[str, list[str]]]:
    """Publish prior PASS work first, then immediately finalize a new PASS."""
    normal = _ORIGINAL_CHILD_COMMANDS(root, runtime)
    kol_publisher = _publisher_argv(root, runtime, "approved_task_publisher_gate.py")
    ak_bermet_publisher = _publisher_argv(
        root,
        runtime,
        "ak_bermet_approved_task_publisher_gate.py",
    )
    return [
        ("kol_approved_publisher_pre", kol_publisher),
        ("ak_bermet_approved_publisher_pre", ak_bermet_publisher),
        *normal,
        ("kol_approved_publisher_post", kol_publisher),
        ("ak_bermet_approved_publisher_post", ak_bermet_publisher),
    ]


def main() -> int:
    control_loop.supervise_telegram_bridge = _dedicated_telegram_service_only
    control_loop.child_commands = _commands_with_publishers
    return control_loop.main()


if __name__ == "__main__":
    raise SystemExit(main())
