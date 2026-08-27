#!/usr/bin/env python3
"""Systemd entrypoint for AI PROF Control Center without embedded Telegram.

Mobile Control runs Telegram as its own dedicated service. This runtime adapter
inserts project-specific post-audit routes before and after the normal Stage 01
pipeline. The established KOL and AK BERMET publishers retain their existing
behavior, Resort OS receives the same bounded approved-task PR publication
route when its reviewed adapter exists, and AI PROF self-maintenance remains
commit-only.

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

try:  # Package import in tests; direct import when run as a service script.
    from orchestrator.universal_task_lifecycle import (
        AdapterStage,
        FixLoopBudget,
        LifecycleStageAdapter,
        OrchestrationSnapshot,
        OrchestrationState,
        StageBinding,
        StageRequest,
        apply_stage_evidence,
        fail_orchestration,
        start_orchestration,
    )
except ImportError:  # pragma: no cover - exercised by the deployed script form
    from universal_task_lifecycle import (  # type: ignore[no-redef]
        AdapterStage,
        FixLoopBudget,
        LifecycleStageAdapter,
        OrchestrationSnapshot,
        OrchestrationState,
        StageBinding,
        StageRequest,
        apply_stage_evidence,
        fail_orchestration,
        start_orchestration,
    )

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


def run_lifecycle_shadow(
    binding: StageBinding,
    budget: FixLoopBudget,
    adapter: LifecycleStageAdapter,
) -> OrchestrationSnapshot:
    """Run injected EXECUTE/TEST/AUDIT adapters as a finite shadow lifecycle.

    Requests contain immutable identity data and prior evidence only. This
    service supplies no commands, paths, queue handles, publication callbacks,
    or repository capability. Adapter errors and malformed evidence terminate
    the shadow fail-closed and never change the normal control-loop result.
    """

    snapshot = start_orchestration(binding, budget)
    maximum_stage_calls = 3 * (budget.max_fix_attempts + 1)
    stage_calls = 0
    while snapshot.state not in (
        OrchestrationState.APPROVED,
        OrchestrationState.FAILED,
    ):
        stage_calls += 1
        if stage_calls > maximum_stage_calls:
            return fail_orchestration(snapshot, "finite orchestration bound reached")

        if snapshot.state is OrchestrationState.FIX_LOOP:
            request_binding = snapshot.binding.next_attempt()
            stage = AdapterStage.EXECUTE
            repair = True
        else:
            request_binding = snapshot.binding
            stage = AdapterStage(snapshot.state.value)
            repair = False
        request = StageRequest(request_binding, snapshot.evidence, repair)

        try:
            if stage is AdapterStage.EXECUTE:
                returned = adapter.execute(request)
            elif stage is AdapterStage.TEST:
                returned = adapter.test(request)
            else:
                returned = adapter.audit(request)
            snapshot = apply_stage_evidence(snapshot, returned)
        except Exception:
            return fail_orchestration(
                snapshot, f"{stage.value.upper()} adapter exception or invalid evidence"
            )
    return snapshot


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _shadow_observation(
    paths: control_loop.ControlPaths,
) -> ControlShadowObservation:
    """Read existing runtime artifacts without invoking mutating status probes."""
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
    """Offer one non-authoritative observation, failing closed on any error."""
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
    """Preserve the established KOL/AK BERMET publisher composition."""
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


def _commands_with_ai_prof_publisher_gate(
    root: Path,
    runtime: Path,
) -> list[tuple[str, list[str]]]:
    """Add reviewed project publishers plus the commit-only AI PROF gate.

    Resort OS is included only when the exact reviewed adapter exists in the
    current Control Center checkout. This keeps old isolated unit fixtures and
    partial maintenance checkouts backward-compatible while the real runtime
    fails closed if the branch was only partially synchronized.
    """
    established = _commands_with_publishers(root, runtime)
    ai_prof_publisher_gate = _publisher_argv(
        root,
        runtime,
        "ai_prof_approved_task_publisher_gate.py",
    )
    resort_script = root / "orchestrator/resort_os_approved_task_publisher_gate.py"
    resort_os_publisher = _publisher_argv(
        root,
        runtime,
        "resort_os_approved_task_publisher_gate.py",
    )
    pre_end = 2
    post_start = len(established) - 2
    pre_extra: list[tuple[str, list[str]]] = []
    post_extra: list[tuple[str, list[str]]] = []
    if resort_script.is_file() and not resort_script.is_symlink():
        pre_extra.append(("resort_os_approved_publisher_pre", resort_os_publisher))
        post_extra.append(("resort_os_approved_publisher_post", resort_os_publisher))
    return [
        *established[:pre_end],
        *pre_extra,
        ("ai_prof_approved_publisher_pre", ai_prof_publisher_gate),
        *established[pre_end:post_start],
        *established[post_start:],
        *post_extra,
        ("ai_prof_approved_publisher_post", ai_prof_publisher_gate),
    ]


def main(
    *,
    shadow_observer: LifecycleShadowObserver | None = None,
    lifecycle_adapter: LifecycleStageAdapter | None = None,
    lifecycle_binding: StageBinding | None = None,
    lifecycle_budget: FixLoopBudget | None = None,
) -> int:
    if (
        lifecycle_adapter is not None
        and lifecycle_binding is not None
        and lifecycle_budget is not None
    ):
        run_lifecycle_shadow(
            lifecycle_binding,
            lifecycle_budget,
            lifecycle_adapter,
        )
    if shadow_observer is None:
        control_loop.supervise_telegram_bridge = _dedicated_telegram_service_only
        control_loop.child_commands = _commands_with_ai_prof_publisher_gate
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
    control_loop.child_commands = _commands_with_ai_prof_publisher_gate
    return control_loop.main()


if __name__ == "__main__":
    raise SystemExit(main())
