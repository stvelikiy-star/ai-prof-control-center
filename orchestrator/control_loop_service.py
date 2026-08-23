#!/usr/bin/env python3
"""Systemd entrypoint for AI PROF Control Center without embedded Telegram.

Mobile Control runs Telegram as its own dedicated service. This runtime adapter
inserts trusted post-audit publishers before and after the normal Stage 01
pipeline. Each publisher is a fail-closed repository-state gate and can act
only on a Stage-01C-approved task for its exact pinned project. Publishers can
commit the approved scope, push that work branch and open a PR; they cannot
merge, deploy, access secrets, or mutate a database.

The service also upgrades the normal Stage 01B Codex command to the V2 adapter,
which preserves the hardened runner while fixing directory-scope instructions
and terminal-reason persistence.
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

import control_loop

_ORIGINAL_CHILD_COMMANDS = control_loop.child_commands


@dataclass(frozen=True)
class ControlShadowObservation:
    """Read-only, non-authoritative view of current control-loop state."""

    running: bool
    paused: bool
    queues: tuple[tuple[str, int], ...]
    heartbeat_state: str | None
    heartbeat_stage: str | None


class LifecycleShadowObserver(Protocol):
    """Observation-only boundary; its return value can never grant authority."""

    def observe(self, observation: ControlShadowObservation) -> object: ...


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _shadow_observation(
    paths: control_loop.ControlPaths,
) -> ControlShadowObservation:
    """Read existing runtime artifacts without invoking mutating status probes.

    ``control_loop.status`` tests the supervisor lock by opening it in a mode
    that creates the file when it is absent.  That is appropriate for the
    control CLI, but not for a lifecycle shadow.  This reader consequently
    uses only existence checks, directory iteration, and an existing
    heartbeat file.  Missing artifacts describe an inactive/empty control
    loop; malformed or unreadable artifacts fail the outer observation closed.
    """
    pause_path = Path(paths.pause)
    heartbeat_path = Path(paths.heartbeat)
    lock_path = Path(paths.lock)
    state_root = pause_path.parent.parent
    queue_root = state_root / "queue"

    queues: list[tuple[str, int]] = []
    if queue_root.exists():
        if queue_root.is_symlink():
            raise ValueError("control-loop queue root must not be a symlink")
        for queue_path in sorted(queue_root.iterdir(), key=lambda item: item.name):
            if queue_path.is_symlink() or not queue_path.is_dir():
                continue
            count = sum(
                1
                for task_path in queue_path.iterdir()
                if (
                    not task_path.is_symlink()
                    and task_path.is_file()
                    and task_path.suffix == ".md"
                )
            )
            queues.append((queue_path.name, count))

    heartbeat: Mapping[str, object] = {}
    if heartbeat_path.exists():
        if heartbeat_path.is_symlink():
            raise ValueError("control-loop heartbeat must not be a symlink")
        decoded = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if not isinstance(decoded, Mapping):
            raise ValueError("control-loop heartbeat must be a JSON object")
        heartbeat = decoded
    return ControlShadowObservation(
        running=lock_path.exists(),
        paused=pause_path.exists(),
        queues=tuple(queues),
        heartbeat_state=_text_or_none(heartbeat.get("state")),
        heartbeat_stage=_text_or_none(heartbeat.get("stage")),
    )


def _observe_lifecycle_shadow(
    paths: control_loop.ControlPaths,
    observer: LifecycleShadowObserver | None,
) -> bool:
    """Offer one non-authoritative observation, failing closed on any error.

    The observer receives no paths, queue items, lifecycle objects, authority
    bindings, or mutation callback.  Its return value is deliberately ignored.
    """
    if observer is None:
        return False
    try:
        observation = _shadow_observation(paths)
        observer.observe(observation)
    except Exception:
        return False
    return True


def _dedicated_telegram_service_only(
    paths: control_loop.ControlPaths,
    stop_event: threading.Event,
    *,
    shadow_observer: LifecycleShadowObserver | None = None,
    **_kwargs,
) -> None:
    """Do not spawn legacy Telegram; wait until Control Center shuts down."""
    _observe_lifecycle_shadow(paths, shadow_observer)
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


def _upgrade_codex_stage01b(
    root: Path,
    commands: list[tuple[str, list[str]]],
) -> list[tuple[str, list[str]]]:
    """Replace only the exact legacy Codex Stage 01B script path with V2."""
    legacy_path = str(root / "orchestrator/codex_stage01b_runner.py")
    v2_path = str(root / "orchestrator/codex_stage01b_runner_v2.py")
    upgraded: list[tuple[str, list[str]]] = []
    for stage, argv in commands:
        upgraded.append(
            (stage, [v2_path if item == legacy_path else item for item in argv])
        )
    return upgraded


def _commands_with_publishers(
    root: Path,
    runtime: Path,
) -> list[tuple[str, list[str]]]:
    """Publish prior PASS work first, run Codex V2, then finalize a new PASS."""
    normal = _upgrade_codex_stage01b(root, _ORIGINAL_CHILD_COMMANDS(root, runtime))
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


def main(*, shadow_observer: LifecycleShadowObserver | None = None) -> int:
    if shadow_observer is None:
        # Preserve the dedicated-service runtime identity on the normal path.
        # Shadow observation is strictly opt-in and must not replace the
        # established adapter when it has not been explicitly configured.
        control_loop.supervise_telegram_bridge = _dedicated_telegram_service_only
        control_loop.child_commands = _commands_with_publishers
        return control_loop.main()

    def dedicated_service(
        paths: control_loop.ControlPaths,
        stop_event: threading.Event,
        **kwargs,
    ) -> None:
        _dedicated_telegram_service_only(
            paths,
            stop_event,
            shadow_observer=shadow_observer,
            **kwargs,
        )

    control_loop.supervise_telegram_bridge = dedicated_service
    control_loop.child_commands = _commands_with_publishers
    return control_loop.main()


if __name__ == "__main__":
    raise SystemExit(main())
