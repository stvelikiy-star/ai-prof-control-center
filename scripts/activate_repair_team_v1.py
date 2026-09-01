#!/usr/bin/env python3
"""Two-phase fail-closed host activation for AI PROF Repair Team V1.

This activation is intentionally unit-only. It never switches Git branches or
updates the live checkout. The caller must first place the Control Center on the
exact approved `origin/main` SHA using the existing reviewed Control Center
release procedure.

Phase `monitor` installs/enables only the passive monitoring timer and proves a
fresh monitoring/incident snapshot. Phase `diagnosis` is separate and requires
a healthy active monitor plus fresh evidence before installing/enabling the
read-only diagnosis -> repair-task shadow timer.

No customer deploy, migration, restart, merge, Git publication or privileged
Repair Team operation is performed by this script. Every systemd mutation is
checkpointed and automatically rolled back on failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

LIVE = Path("/home/agent/projects/ai-prof-control-center")
STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
BACKUP_ROOT = Path("/home/agent/ai-prof-backups/control-center")
UNIT_DIR = Path("/etc/systemd/system")
EXPECTED_REPOSITORY = "stvelikiy-star/ai-prof-control-center"
EXPECTED_OWNER = "stvelikiy-star"
EXPECTED_DEFAULT_BRANCH = "main"
EXPECTED_PRIVATE = "false"
EXPECTED_VISIBILITY = "public"

MONITOR_SERVICE = "ai-prof-repair-monitor.service"
MONITOR_TIMER = "ai-prof-repair-monitor.timer"
DIAGNOSIS_SERVICE = "ai-prof-repair-diagnosis.service"
DIAGNOSIS_TIMER = "ai-prof-repair-diagnosis.timer"
REPAIR_UNITS = (
    MONITOR_SERVICE,
    MONITOR_TIMER,
    DIAGNOSIS_SERVICE,
    DIAGNOSIS_TIMER,
)
PHASE_UNITS = {
    "monitor": (MONITOR_SERVICE, MONITOR_TIMER),
    "diagnosis": (DIAGNOSIS_SERVICE, DIAGNOSIS_TIMER),
}
SUPPORTED_ENABLED_STATES = {"enabled", "disabled", "static", "indirect", "not-found"}
FRESH_EVIDENCE_SECONDS = 300


class ActivationError(RuntimeError):
    pass


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=capture,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ActivationError(f"unable to run {argv[0]}: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise ActivationError(f"command failed ({argv[0]} rc={result.returncode}): {detail[:1200]}")
    return result


def git(*args: str, capture: bool = True) -> str:
    result = run(["git", *args], cwd=LIVE, capture=capture, timeout=120)
    return (result.stdout or "").strip()


def sudo(*args: str, capture: bool = False, check: bool = True, timeout: int = 120):
    return run(["sudo", "-n", *args], capture=capture, check=check, timeout=timeout)


def systemd_state(verb: str, name: str) -> str:
    result = sudo("systemctl", verb, name, capture=True, check=False, timeout=30)
    value = (result.stdout or result.stderr or "").strip().splitlines()
    if value:
        return value[-1].strip()
    if verb == "is-active":
        return "inactive"
    if verb == "is-enabled":
        return "not-found" if result.returncode == 4 else "disabled"
    return "unknown"


def unit_snapshot() -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for name in REPAIR_UNITS:
        enabled = systemd_state("is-enabled", name)
        active = systemd_state("is-active", name)
        if enabled not in SUPPORTED_ENABLED_STATES:
            raise ActivationError(f"unsupported pre-existing enabled state for {name}: {enabled}")
        snapshot[name] = {"enabled": enabled, "active": active}
    return snapshot


def expected_identity() -> str:
    return "\t".join(
        (
            EXPECTED_REPOSITORY,
            EXPECTED_OWNER,
            EXPECTED_DEFAULT_BRANCH,
            EXPECTED_PRIVATE,
            EXPECTED_VISIBILITY,
        )
    )


def verify_repository_identity() -> None:
    result = run(
        [
            "gh",
            "api",
            f"repos/{EXPECTED_REPOSITORY}",
            "--jq",
            "[.full_name, .owner.login, .default_branch, (.private|tostring), .visibility] | @tsv",
        ],
        capture=True,
        timeout=60,
    )
    actual = (result.stdout or "").strip()
    if actual != expected_identity():
        raise ActivationError(
            f"Control Center GitHub identity mismatch: expected {expected_identity()!r}, got {actual!r}"
        )


def _unit_source(name: str) -> Path:
    path = LIVE / "systemd" / name
    if path.is_symlink() or not path.is_file():
        raise ActivationError(f"staged Repair Team unit is missing or unsafe: {name}")
    return path


def validate_staged_units() -> None:
    """Reassert the V1 read-only unit boundary before any systemd write."""
    monitor = _unit_source(MONITOR_SERVICE).read_text(encoding="utf-8")
    diagnosis = _unit_source(DIAGNOSIS_SERVICE).read_text(encoding="utf-8")
    monitor_timer = _unit_source(MONITOR_TIMER).read_text(encoding="utf-8")
    diagnosis_timer = _unit_source(DIAGNOSIS_TIMER).read_text(encoding="utf-8")

    for required in ("incident_engine.py", "diagnosis_packet.py"):
        if required not in monitor:
            raise ActivationError(f"monitor unit missing required runner: {required}")
    for required in ("incident_diagnosis_runner.py", "repair_task_bridge.py"):
        if required not in diagnosis:
            raise ActivationError(f"diagnosis unit missing required runner: {required}")
    for name, text in ((MONITOR_SERVICE, monitor), (DIAGNOSIS_SERVICE, diagnosis)):
        if "ProtectSystem=strict" not in text or "ProtectHome=read-only" not in text:
            raise ActivationError(f"Repair Team unit lost filesystem hardening: {name}")
        expected_rw = f"ReadWritePaths={STATE_ROOT}"
        rw_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("ReadWritePaths=")]
        if rw_lines != [expected_rw]:
            raise ActivationError(f"Repair Team unit write boundary drifted: {name}: {rw_lines}")
        forbidden = (
            "operations_runner.py",
            "repair_operations_bridge.py",
            "release_flow.py",
            "supabase db push",
            "docker restart",
            "git push",
        )
        if any(token in text for token in forbidden):
            raise ActivationError(f"privileged runner leaked into staged Repair Team unit: {name}")
    if f"Unit={MONITOR_SERVICE}" not in monitor_timer:
        raise ActivationError("monitor timer targets unexpected service")
    if f"Unit={DIAGNOSIS_SERVICE}" not in diagnosis_timer:
        raise ActivationError("diagnosis timer targets unexpected service")


def verify_preconditions(approved_sha: str, phase: str) -> dict[str, Any]:
    if phase not in PHASE_UNITS:
        raise ActivationError(f"unsupported activation phase: {phase}")
    if os.geteuid() == 0:
        raise ActivationError("run as the normal agent user, not root")
    if LIVE.is_symlink() or not LIVE.is_dir() or not (LIVE / ".git").is_dir():
        raise ActivationError("live Control Center checkout is unavailable")
    if STATE_ROOT.is_symlink() or not STATE_ROOT.is_dir():
        raise ActivationError("Control Center state root is unavailable or unsafe")
    for command in ("git", "python3", "gh", "sudo", "systemctl"):
        if shutil.which(command) is None:
            raise ActivationError(f"required command unavailable: {command}")
    if not re.fullmatch(r"[0-9a-f]{40}", approved_sha):
        raise ActivationError("--approved-sha must be the exact 40-char lowercase main SHA")
    if git("status", "--porcelain"):
        raise ActivationError("live Control Center worktree is dirty")
    branch = git("branch", "--show-current")
    head = git("rev-parse", "HEAD")
    if branch != "main":
        raise ActivationError(f"Repair Team activation requires live main, got {branch!r}")
    if head != approved_sha:
        raise ActivationError(f"live HEAD does not match approved SHA: head={head}, approved={approved_sha}")

    verify_repository_identity()
    git("fetch", "--prune", "origin", "main", capture=False)
    remote_sha = git("rev-parse", "origin/main")
    if remote_sha != approved_sha:
        raise ActivationError(f"origin/main moved: expected {approved_sha}, got {remote_sha}")
    if git("status", "--porcelain"):
        raise ActivationError("live worktree changed during activation preflight")
    validate_staged_units()
    sudo("true")
    states = unit_snapshot()

    if phase == "monitor":
        diagnosis_state = states[DIAGNOSIS_TIMER]
        if diagnosis_state["enabled"] == "enabled" or diagnosis_state["active"] == "active":
            raise ActivationError("diagnosis timer is already enabled/active before monitor canary")
    else:
        monitor_state = states[MONITOR_TIMER]
        if monitor_state["enabled"] != "enabled" or monitor_state["active"] != "active":
            raise ActivationError("diagnosis phase requires active enabled monitor timer")
        verify_monitor_evidence()
        verify_zero_privileged_bindings()

    return {"branch": branch, "head": head, "unit_states": states, "phase": phase}


def _write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def create_checkpoint(meta: dict[str, Any], approved_sha: str, phase: str) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_ROOT / f"repair-team-{phase}-{stamp}"
    if backup.exists() or backup.is_symlink():
        raise ActivationError("Repair Team checkpoint path already exists or is unsafe")
    backup.mkdir(parents=True, mode=0o700)
    _write_private(backup / "APPROVED_SHA", approved_sha + "\n")
    _write_private(backup / "PHASE", phase + "\n")
    _write_private(backup / "PREVIOUS_BRANCH", str(meta["branch"]) + "\n")
    _write_private(backup / "PREVIOUS_SHA", str(meta["head"]) + "\n")
    _write_private(
        backup / "UNIT_STATES.json",
        json.dumps(meta["unit_states"], sort_keys=True, indent=2) + "\n",
    )
    run(["git", "bundle", "create", str(backup / "repository.bundle"), "HEAD"], cwd=LIVE)
    (backup / "repository.bundle").chmod(0o600)
    for name in REPAIR_UNITS:
        target = UNIT_DIR / name
        existed = target.is_file() and not target.is_symlink()
        _write_private(backup / f"{name}.existed", "yes\n" if existed else "no\n")
        if (target.exists() or target.is_symlink()) and not existed:
            raise ActivationError(f"pre-existing unit path is not a regular file: {target}")
        if existed:
            saved = backup / name
            sudo("cp", "--preserve=mode,timestamps", str(target), str(saved))
            sudo("chown", f"{os.getuid()}:{os.getgid()}", str(saved))
            saved.chmod(0o600)
    return backup


def install_phase_units(phase: str) -> None:
    for name in PHASE_UNITS[phase]:
        source = _unit_source(name)
        sudo("install", "-m", "0644", str(source), str(UNIT_DIR / name))
    sudo("systemctl", "daemon-reload")


def _timer_for_phase(phase: str) -> str:
    return MONITOR_TIMER if phase == "monitor" else DIAGNOSIS_TIMER


def _service_for_phase(phase: str) -> str:
    return MONITOR_SERVICE if phase == "monitor" else DIAGNOSIS_SERVICE


def activate_phase_runtime(phase: str) -> None:
    timer = _timer_for_phase(phase)
    service = _service_for_phase(phase)
    sudo("systemctl", "enable", "--now", timer)
    # Explicit one-shot canary. Unit SuccessExitStatus=0 1 converts expected
    # observed-failure states into a successful systemd start while config/
    # runtime errors still fail the activation.
    sudo("systemctl", "start", service, timeout=1300)
    if systemd_state("is-enabled", timer) != "enabled":
        raise ActivationError(f"Repair Team timer is not enabled after activation: {timer}")
    if systemd_state("is-active", timer) != "active":
        raise ActivationError(f"Repair Team timer is not active after activation: {timer}")


def _load_fresh_json(path: Path, expected_version: int) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ActivationError(f"required Repair Team evidence is missing or unsafe: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ActivationError(f"invalid Repair Team evidence {path.name}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != expected_version:
        raise ActivationError(f"Repair Team evidence schema mismatch: {path.name}")
    generated = payload.get("generated_at")
    if not isinstance(generated, str):
        raise ActivationError(f"Repair Team evidence timestamp missing: {path.name}")
    try:
        instant = dt.datetime.fromisoformat(generated)
    except ValueError as exc:
        raise ActivationError(f"Repair Team evidence timestamp invalid: {path.name}") from exc
    if instant.tzinfo is None:
        raise ActivationError(f"Repair Team evidence timestamp is naive: {path.name}")
    age = (dt.datetime.now(dt.timezone.utc) - instant.astimezone(dt.timezone.utc)).total_seconds()
    if age < -30 or age > FRESH_EVIDENCE_SECONDS:
        raise ActivationError(f"Repair Team evidence is stale: {path.name}: age={age:.1f}s")
    return payload


def verify_monitor_evidence() -> dict[str, Any]:
    snapshot = _load_fresh_json(STATE_ROOT / "monitoring" / "latest.json", 1)
    summary = _load_fresh_json(STATE_ROOT / "incidents" / "summary.json", 1)
    observations = snapshot.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ActivationError("monitor canary produced no observations")
    if not any(
        isinstance(item, dict) and item.get("project_id") == "ai-prof-control-center"
        for item in observations
    ):
        raise ActivationError("monitor canary lacks AI PROF Control Center observation")
    if not isinstance(summary.get("open_count"), int) or not isinstance(summary.get("resolved_count"), int):
        raise ActivationError("incident summary counters are invalid")
    return {
        "observations": len(observations),
        "open_incidents": summary["open_count"],
        "resolved_incidents": summary["resolved_count"],
    }


def verify_zero_privileged_bindings() -> None:
    path = LIVE / "orchestrator" / "repair_operation_bindings.json"
    if path.is_symlink() or not path.is_file():
        raise ActivationError("privileged operation binding registry is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ActivationError(f"invalid privileged operation binding registry: {exc}") from exc
    if payload.get("version") != 1 or payload.get("bindings") != []:
        raise ActivationError("Repair Team V1 diagnosis activation requires zero privileged bindings")


def _restore_unit_files(backup: Path) -> None:
    for name in REPAIR_UNITS:
        existed_path = backup / f"{name}.existed"
        try:
            existed = existed_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ActivationError(f"checkpoint missing unit existence marker: {name}") from exc
        target = UNIT_DIR / name
        saved = backup / name
        if existed == "yes":
            if not saved.is_file() or saved.is_symlink():
                raise ActivationError(f"checkpoint unit backup missing or unsafe: {name}")
            sudo("install", "-m", "0644", str(saved), str(target))
        elif existed == "no":
            sudo("rm", "-f", str(target))
        else:
            raise ActivationError(f"invalid checkpoint unit marker for {name}: {existed!r}")
    sudo("systemctl", "daemon-reload")


def _restore_unit_states(backup: Path) -> None:
    try:
        states = json.loads((backup / "UNIT_STATES.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ActivationError(f"invalid checkpoint unit states: {exc}") from exc
    if not isinstance(states, dict) or set(states) != set(REPAIR_UNITS):
        raise ActivationError("checkpoint unit state set is invalid")
    # Stop first to avoid a restored timer firing during state reconstruction.
    for name in reversed(REPAIR_UNITS):
        sudo("systemctl", "stop", name, check=False)
    for name in REPAIR_UNITS:
        state = states[name]
        if not isinstance(state, dict):
            raise ActivationError(f"invalid checkpoint state for {name}")
        enabled = state.get("enabled")
        active = state.get("active")
        if name.endswith(".timer"):
            if enabled == "enabled":
                sudo("systemctl", "enable", name)
            else:
                sudo("systemctl", "disable", name, check=False)
        if active == "active":
            sudo("systemctl", "start", name)


def rollback(backup: Path) -> None:
    for name in reversed(REPAIR_UNITS):
        sudo("systemctl", "stop", name, check=False)
    _restore_unit_files(backup)
    _restore_unit_states(backup)


def verify_post_activation(approved_sha: str, phase: str, before_status: str) -> dict[str, Any]:
    if git("rev-parse", "HEAD") != approved_sha or git("branch", "--show-current") != "main":
        raise ActivationError("Repair Team activation changed live Git identity")
    if git("status", "--porcelain") != before_status:
        raise ActivationError("Repair Team activation changed live worktree")
    if phase == "monitor":
        return verify_monitor_evidence()
    verify_monitor_evidence()
    verify_zero_privileged_bindings()
    return {
        "monitor_timer": systemd_state("is-active", MONITOR_TIMER),
        "diagnosis_timer": systemd_state("is-active", DIAGNOSIS_TIMER),
        "privileged_bindings": 0,
    }


def activate(approved_sha: str, phase: str) -> tuple[Path, dict[str, Any]]:
    meta = verify_preconditions(approved_sha, phase)
    before_status = git("status", "--porcelain")
    backup = create_checkpoint(meta, approved_sha, phase)
    try:
        install_phase_units(phase)
        activate_phase_runtime(phase)
        evidence = verify_post_activation(approved_sha, phase, before_status)
    except Exception as exc:
        try:
            rollback(backup)
        except Exception as rollback_exc:
            raise ActivationError(
                f"Repair Team activation failed: {exc}; automatic rollback ALSO failed: "
                f"{rollback_exc}; checkpoint={backup}"
            ) from rollback_exc
        if isinstance(exc, ActivationError):
            raise
        raise ActivationError(str(exc)) from exc
    return backup, evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-sha", required=True)
    parser.add_argument("--phase", required=True, choices=tuple(PHASE_UNITS))
    args = parser.parse_args()
    try:
        checkpoint, evidence = activate(args.approved_sha.strip(), args.phase)
    except ActivationError as exc:
        print(f"REPAIR_TEAM_ACTIVATION_V1: BLOCKED: {exc}")
        return 2
    print("REPAIR_TEAM_ACTIVATION_V1: PASS")
    print(f"PHASE: {args.phase}")
    print(f"ACTIVE_SHA: {git('rev-parse', 'HEAD')}")
    print(f"ROLLBACK_CHECKPOINT: {checkpoint}")
    print("EVIDENCE: " + json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
