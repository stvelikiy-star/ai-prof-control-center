#!/usr/bin/env python3
"""Fail-closed host activation V2 for the repaired AI PROF Control Center.

V2 reuses the reviewed V1 checkpoint, ff-only sync, release tests, unit install,
service health and rollback implementation. It replaces only the stale GitHub
privacy precondition and adds pre-live invariants required for the KÖL Night
Watch validation:

- the canonical Control Center repository identity is exact and currently public;
- the runtime pause guard must already exist before any activation work;
- the legacy KÖL V1 task must exist exactly once and remain in pending;
- after unit installation, effective systemd WorkingDirectory/ExecStart must
  still point at the canonical Control Center after all drop-ins are applied;
- after services start, the Control Center heartbeat must report `paused`, and
  systemd MainPID, heartbeat PID, /proc cmdline and cwd must identify the same
  canonical Control Center process.

No task is unpaused, cancelled, created, merged or deployed by this script.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import activate_mobile_control_v1 as v1

EXPECTED_REPOSITORY = "stvelikiy-star/ai-prof-control-center"
EXPECTED_OWNER = "stvelikiy-star"
EXPECTED_DEFAULT_BRANCH = "main"
EXPECTED_PRIVATE = "false"
EXPECTED_VISIBILITY = "public"
STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
PAUSE_FILE = STATE_ROOT / "run" / "paused"
CANONICAL_LEGACY_KOL_TASK = "KOL_TRAVEL_PLATFORM_20260829T095522Z_CFB967"
CONTROL_SERVICE = "ai-prof-control-center.service"
QUEUE_NAMES = (
    "pending",
    "active",
    "review",
    "pending_codex",
    "approved",
    "completed",
    "blocked",
    "failed",
    "cancelled",
)
PAUSED_HEALTH_TIMEOUT_SECONDS = 30


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


def canonical_control_script() -> Path:
    return v1.LIVE / "orchestrator" / "control_loop_service_night.py"


def systemd_property(name: str) -> str:
    result = v1.sudo(
        "systemctl",
        "show",
        CONTROL_SERVICE,
        f"--property={name}",
        "--value",
        capture=True,
    )
    return (result.stdout or "").strip()


def verify_effective_control_unit() -> dict[str, str]:
    """Reject stale drop-ins that redirect the effective service elsewhere."""
    working_directory = systemd_property("WorkingDirectory")
    exec_start = systemd_property("ExecStart")
    drop_ins = systemd_property("DropInPaths")
    expected_root = str(v1.LIVE)
    expected_script = str(canonical_control_script())

    if working_directory != expected_root:
        raise v1.ActivationError(
            "effective Control Center WorkingDirectory mismatch after systemd drop-ins: "
            f"expected {expected_root!r}, got {working_directory!r}; drop-ins={drop_ins!r}"
        )
    if expected_script not in exec_start:
        raise v1.ActivationError(
            "effective Control Center ExecStart does not use canonical runtime: "
            f"expected script {expected_script!r}; exec_start={exec_start!r}; "
            f"drop-ins={drop_ins!r}"
        )
    if "--root" in exec_start and f"--root {expected_root}" not in exec_start:
        raise v1.ActivationError(
            "effective Control Center ExecStart overrides --root away from canonical checkout: "
            f"exec_start={exec_start!r}; drop-ins={drop_ins!r}"
        )
    return {
        "working_directory": working_directory,
        "exec_start": exec_start,
        "drop_ins": drop_ins,
    }


def read_process_identity(pid: int) -> tuple[str, Path]:
    proc = Path("/proc") / str(pid)
    try:
        cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace"
        ).strip()
        cwd = (proc / "cwd").resolve(strict=True)
    except OSError as exc:
        raise v1.ActivationError(
            f"cannot inspect Control Center process identity for pid={pid}: {exc}"
        ) from exc
    return cmdline, cwd


def verify_running_control_process(heartbeat: dict) -> dict[str, object]:
    """Bind systemd, heartbeat and /proc identity to the canonical runtime."""
    main_pid_text = systemd_property("MainPID")
    if not main_pid_text.isdigit() or int(main_pid_text) <= 1:
        raise v1.ActivationError(
            f"invalid effective Control Center MainPID: {main_pid_text!r}"
        )
    main_pid = int(main_pid_text)
    heartbeat_pid = heartbeat.get("pid")
    if heartbeat_pid != main_pid:
        raise v1.ActivationError(
            "Control Center heartbeat PID does not match systemd MainPID: "
            f"heartbeat_pid={heartbeat_pid!r}, main_pid={main_pid}"
        )

    cmdline, cwd = read_process_identity(main_pid)
    expected_root = v1.LIVE
    expected_script = str(canonical_control_script())
    if expected_script not in cmdline:
        raise v1.ActivationError(
            "running Control Center process does not use canonical runtime script: "
            f"pid={main_pid}, cmdline={cmdline!r}"
        )
    if cwd != expected_root:
        raise v1.ActivationError(
            "running Control Center cwd is not canonical checkout: "
            f"pid={main_pid}, expected={str(expected_root)!r}, got={str(cwd)!r}"
        )
    return {"pid": main_pid, "cmdline": cmdline, "cwd": str(cwd)}


def verify_repository_identity() -> None:
    result = v1.run(
        [
            "gh",
            "api",
            f"repos/{EXPECTED_REPOSITORY}",
            "--jq",
            "[.full_name, .owner.login, .default_branch, (.private|tostring), .visibility] | @tsv",
        ],
        capture=True,
    )
    actual = (result.stdout or "").strip()
    if actual != expected_identity():
        raise v1.ActivationError(
            "Control Center GitHub identity mismatch: "
            f"expected {expected_identity()!r}, got {actual!r}"
        )


def legacy_kol_task_queue() -> str:
    matches = []
    for queue in QUEUE_NAMES:
        path = STATE_ROOT / "queue" / queue / f"{CANONICAL_LEGACY_KOL_TASK}.md"
        if path.is_file() and not path.is_symlink():
            matches.append(queue)
    if len(matches) != 1:
        raise v1.ActivationError(
            "legacy KÖL V1 task must exist in exactly one queue before activation: "
            f"matches={matches}"
        )
    return matches[0]


def verify_preconditions(approved_sha: str) -> dict[str, object]:
    if os.geteuid() == 0:
        raise v1.ActivationError("run as the normal agent user, not root")
    if v1.LIVE.is_symlink() or not v1.LIVE.is_dir() or not (v1.LIVE / ".git").exists():
        raise v1.ActivationError("live Control Center checkout is unavailable")
    if shutil.which("git") is None or shutil.which("python3") is None:
        raise v1.ActivationError("git and python3 are required")
    if shutil.which("gh") is None:
        raise v1.ActivationError("GitHub CLI is required for the gateway")
    if not approved_sha or len(approved_sha) != 40:
        raise v1.ActivationError("--approved-sha must be the exact 40-char main SHA")
    if not PAUSE_FILE.is_file() or PAUSE_FILE.is_symlink():
        raise v1.ActivationError("pre-live pause guard is missing or unsafe")
    if legacy_kol_task_queue() != "pending":
        raise v1.ActivationError("legacy KÖL V1 task must remain pending before activation")

    status = v1.git("status", "--porcelain")
    if status:
        raise v1.ActivationError(
            "live Control Center worktree is dirty; preserve/reconcile it before activation"
        )

    branch = v1.git("branch", "--show-current")
    head = v1.git("rev-parse", "HEAD")
    origin = v1.git("remote", "get-url", "origin")
    if not origin:
        raise v1.ActivationError("origin remote is missing")

    verify_repository_identity()

    v1.git("fetch", "--prune", "origin", "main", capture=False)
    remote_sha = v1.git("rev-parse", "origin/main")
    if remote_sha != approved_sha:
        raise v1.ActivationError(
            f"origin/main moved: expected {approved_sha}, got {remote_sha}"
        )
    if v1.run(
        ["git", "merge-base", "--is-ancestor", head, approved_sha],
        cwd=v1.LIVE,
        check=False,
    ).returncode != 0:
        raise v1.ActivationError(
            "current live HEAD is not an ancestor of approved main; refusing to overwrite local history"
        )

    v1.sudo("true")
    active = {name: v1.service_active(name) for name in v1.SERVICES}
    return {"branch": branch, "head": head, "active": active}


def read_heartbeat() -> dict:
    heartbeat = STATE_ROOT / "run" / "heartbeat.json"
    try:
        payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def verify_paused_runtime() -> dict:
    if not PAUSE_FILE.is_file() or PAUSE_FILE.is_symlink():
        raise v1.ActivationError("pause guard disappeared during activation")
    if legacy_kol_task_queue() != "pending":
        raise v1.ActivationError("legacy KÖL V1 task moved while runtime should be paused")

    verify_effective_control_unit()
    deadline = time.monotonic() + PAUSED_HEALTH_TIMEOUT_SECONDS
    last_heartbeat: dict = {}
    while time.monotonic() < deadline:
        all_active = all(v1.service_active(name) for name in v1.SERVICES)
        last_heartbeat = read_heartbeat()
        if all_active and last_heartbeat.get("state") == "paused":
            verify_running_control_process(last_heartbeat)
            return last_heartbeat
        time.sleep(1)

    raise v1.ActivationError(
        "services did not reach paused heartbeat after activation: "
        f"heartbeat={last_heartbeat!r}"
    )


def activate(approved_sha: str) -> tuple[Path, dict]:
    original = v1.verify_preconditions
    v1.verify_preconditions = verify_preconditions
    try:
        checkpoint = v1.activate(approved_sha)
    finally:
        v1.verify_preconditions = original
    heartbeat = verify_paused_runtime()
    return checkpoint, heartbeat


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-sha", required=True)
    args = parser.parse_args()
    try:
        checkpoint, heartbeat = activate(args.approved_sha.strip())
    except v1.ActivationError as exc:
        print(f"MOBILE_CONTROL_ACTIVATION_V2: BLOCKED: {exc}")
        return 2

    print("MOBILE_CONTROL_ACTIVATION_V2: PASS")
    print(f"ACTIVE_SHA: {v1.git('rev-parse', 'HEAD')}")
    print(f"ROLLBACK_CHECKPOINT: {checkpoint}")
    print("PAUSE_GUARD: PRESENT")
    print(f"HEARTBEAT_STATE: {heartbeat.get('state')}")
    print(f"LEGACY_KOL_TASK_QUEUE: {legacy_kol_task_queue()}")
    for name in v1.SERVICES:
        print(f"SERVICE {name}: {'active' if v1.service_active(name) else 'inactive'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
