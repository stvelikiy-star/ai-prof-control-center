#!/usr/bin/env python3
"""Systemd entrypoint for AI PROF Control Center without embedded Telegram.

The historical control loop supervises legacy ``telegram_bridge.py`` internally.
Mobile Control V1 runs Telegram V2 as its own dedicated systemd service, so this
entrypoint disables only that embedded bridge supervisor while preserving the
existing control-loop task pipeline and safety behavior.
"""
from __future__ import annotations

import threading

import control_loop


def _dedicated_telegram_service_only(
    _paths: control_loop.ControlPaths,
    stop_event: threading.Event,
    **_kwargs,
) -> None:
    """Do not spawn legacy Telegram; wait until Control Center shuts down."""
    stop_event.wait()


def main() -> int:
    control_loop.supervise_telegram_bridge = _dedicated_telegram_service_only
    return control_loop.main()


if __name__ == "__main__":
    raise SystemExit(main())
