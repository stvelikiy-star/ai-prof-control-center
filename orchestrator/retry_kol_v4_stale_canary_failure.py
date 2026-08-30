#!/usr/bin/env python3
"""One bounded second retry for the proven stale Night Watch canary incident.

This helper is deliberately narrower than ``retry_kol_v4_runtime_failure``.
It accepts only the exact KÖL V4 task that already carries Retry-Attempt: 1,
failed again with the same pre-execution Stage 01B required-check mismatch,
and has independent evidence that the stale systemd canary override was removed
and preserved. A third retry is never accepted.

No commit, push, merge, deployment, database, secret, payment, production, or
cross-project authority is granted by this helper.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import retry_kol_v4_runtime_failure as base
from runtime_paths import DEFAULT_STATE_ROOT, initialize

DEFAULT_ROOT = base.DEFAULT_ROOT
KOL_PROJECT = base.KOL_PROJECT
FIRST_RETRY_REASON = "stage01b-required-check-runtime-repaired"
SECOND_RETRY_REASON = "stale-night-watch-canary-runtime-removed"
STALE_DROPIN = Path(
    "/etc/systemd/system/ai-prof-control-center.service.d/90-night-watch-canary.conf"
)
STALE_WORKDIR = "/home/agent/projects/ai-prof-control-center-night-watch"
STALE_EXEC = (
    "/home/agent/projects/ai-prof-control-center-night-watch/"
    "orchestrator/control_loop_service_night.py"
)
SECOND_REASON_RE = re.compile(r"(?mi)^[ \t]*Retry-Second-Reason:")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SELF_TEST_MARKER = "KOL_V4_STALE_CANARY_RETRY_SELF_TEST_PASS"


class StaleCanaryRetryError(base.RetryError):
    pass


def _single_field(text: str, name: str) -> str:
    try:
        return base.field(text, name)
    except base.RetryError as exc:
        raise StaleCanaryRetryError(str(exc)) from exc


def _validate_common_v4(text: str, task_id: str) -> dict[str, str]:
    critical = {name: _single_field(text, name) for name in base.CRITICAL_FIELDS}
    if critical["Task-ID"] != task_id:
        raise StaleCanaryRetryError("task filename and Task-ID differ")
    if critical["Project-Path"] != str(KOL_PROJECT):
        raise StaleCanaryRetryError("retry is restricted to the canonical KÖL checkout")
    if critical["Base-Branch"] != base.KOL_BASE_BRANCH:
        raise StaleCanaryRetryError("KÖL retry requires main base branch")
    if critical["Publication-Contract-Version"] != "4":
        raise StaleCanaryRetryError("retry requires KÖL V4 publication contract")
    if critical["Publication-Action"] != "pull-request":
        raise StaleCanaryRetryError("retry requires pull-request publication action")
    if critical["Publication-Repository"] != "stvelikiy-star/kol-travel-platform":
        raise StaleCanaryRetryError("retry publication repository mismatch")
    if critical["Required-Checks"] != base.EXPECTED_REQUIRED_CHECKS:
        raise StaleCanaryRetryError("retry Required-Checks differ from proven live failure")
    source_issue = critical["Publication-Source-Issue"]
    if not source_issue.isdigit() or int(source_issue) <= 0:
        raise StaleCanaryRetryError("invalid V4 source issue")
    if critical["Work-Branch"] != f"feature/chatgpt-issue-{source_issue}":
        raise StaleCanaryRetryError("work branch does not match V4 source issue")
    return critical


def validate_second_failure(text: str, task_id: str) -> dict[str, str]:
    critical = _validate_common_v4(text, task_id)
    attempts = base.RETRY_ATTEMPT_RE.findall(text)
    if attempts != ["1"]:
        raise StaleCanaryRetryError("second retry requires exactly Retry-Attempt: 1")
    if _single_field(text, "Retry-Reason") != FIRST_RETRY_REASON:
        raise StaleCanaryRetryError("first retry reason is not the repaired Stage 01B contract")
    previous_sha = _single_field(text, "Retry-Previous-Failure-SHA256")
    if not HEX64_RE.fullmatch(previous_sha):
        raise StaleCanaryRetryError("first retry failure digest is invalid")
    first_backup = Path(_single_field(text, "Retry-Evidence-Backup"))
    if not first_backup.is_absolute():
        raise StaleCanaryRetryError("first retry evidence backup must be absolute")
    if SECOND_REASON_RE.search(text):
        raise StaleCanaryRetryError("stale-canary second retry was already prepared")
    failure = _single_field(text, "Failure-Reason")
    if (
        "CODEX_STAGE01B_FAILED" not in failure
        or base.EXPECTED_FAILURE_FRAGMENT not in failure
    ):
        raise StaleCanaryRetryError(
            "second failure is not the same proven Stage 01B runtime-contract mismatch"
        )
    return critical


def validate_stale_canary_evidence(
    backup: Path,
    *,
    active_dropin: Path = STALE_DROPIN,
) -> Path:
    if active_dropin.exists():
        raise StaleCanaryRetryError("stale Night Watch canary drop-in is still active")
    try:
        resolved = backup.resolve(strict=True)
    except OSError as exc:
        raise StaleCanaryRetryError("stale canary evidence backup is unavailable") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise StaleCanaryRetryError("stale canary evidence backup is not a safe directory")
    disabled = resolved / "90-night-watch-canary.conf.disabled"
    if not disabled.is_file() or disabled.is_symlink():
        raise StaleCanaryRetryError("disabled stale canary evidence file is missing")
    text = disabled.read_text(encoding="utf-8", errors="strict")
    if STALE_WORKDIR not in text or STALE_EXEC not in text:
        raise StaleCanaryRetryError("stale canary backup does not prove the old runtime override")
    metadata = resolved / "metadata.txt"
    if not metadata.is_file() or metadata.is_symlink():
        raise StaleCanaryRetryError("stale canary metadata is missing")
    metadata_text = metadata.read_text(encoding="utf-8", errors="strict")
    if "reason=stale systemd canary overrode canonical Control Center ExecStart" not in metadata_text:
        raise StaleCanaryRetryError("stale canary metadata reason is missing")
    digest = hashlib.sha256(disabled.read_bytes()).hexdigest()
    if f"sha256={digest}" not in metadata_text:
        raise StaleCanaryRetryError("stale canary metadata digest does not match evidence")
    return resolved


def render_second_retry_task(original: str, retry_backup: Path, canary_backup: Path) -> str:
    critical_before = {name: _single_field(original, name) for name in base.CRITICAL_FIELDS}
    updated, count = base.FAILURE_REASON_RE.subn("", original, count=1)
    if count != 1:
        raise StaleCanaryRetryError("unable to remove exactly one current Failure-Reason")
    updated, count = base.RETRY_ATTEMPT_RE.subn("Retry-Attempt: 2", updated, count=1)
    if count != 1:
        raise StaleCanaryRetryError("unable to advance exactly one Retry-Attempt marker")
    updated = updated.rstrip() + "\n"
    failure_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    updated += f"Retry-Second-Reason: {SECOND_RETRY_REASON}\n"
    updated += f"Retry-Second-Previous-Failure-SHA256: {failure_sha}\n"
    updated += f"Retry-Second-Evidence-Backup: {retry_backup}\n"
    updated += f"Retry-Stale-Canary-Backup: {canary_backup}\n"
    critical_after = {name: _single_field(updated, name) for name in base.CRITICAL_FIELDS}
    if critical_after != critical_before:
        raise StaleCanaryRetryError("second retry changed V4 authority metadata")
    if base.FAILURE_REASON_RE.search(updated):
        raise StaleCanaryRetryError("current Failure-Reason remains in second retry task")
    if base.RETRY_ATTEMPT_RE.findall(updated) != ["2"]:
        raise StaleCanaryRetryError("second retry did not produce exactly Retry-Attempt: 2")
    return updated


def retry_task(
    runtime: Path,
    task_id: str,
    stale_canary_backup: Path,
    *,
    project: Path = KOL_PROJECT,
    active_dropin: Path = STALE_DROPIN,
) -> dict[str, str]:
    base.validate_runtime_paused(runtime)
    locations = base.task_locations(runtime, task_id)
    if len(locations) != 1 or locations[0][0] != "failed":
        raise StaleCanaryRetryError("task must exist exactly once in failed")
    source = locations[0][1]
    original = source.read_text(encoding="utf-8", errors="strict")
    validate_second_failure(original, task_id)
    base.validate_clean_kol_checkout(project)
    base.stage01b_v2.verify_v2_required_check_contract()
    canary_backup = validate_stale_canary_evidence(
        stale_canary_backup,
        active_dropin=active_dropin,
    )

    pending = runtime / "queue" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    target = pending / source.name
    if target.exists():
        raise StaleCanaryRetryError("pending retry destination already exists")

    retry_backup = base.backup_evidence(runtime, source, task_id)
    retry_text = render_second_retry_task(original, retry_backup, canary_backup)
    base.atomic_write(source, retry_text)
    os.rename(source, target)
    base.fsync_directory(source.parent)
    base.fsync_directory(target.parent)

    locations_after = base.task_locations(runtime, task_id)
    if locations_after != [("pending", target)]:
        raise StaleCanaryRetryError("second retry did not produce one pending task")
    return {
        "task_id": task_id,
        "queue": "pending",
        "retry_attempt": "2",
        "backup": str(retry_backup),
        "stale_canary_backup": str(canary_backup),
        "path": str(target),
    }


def run_self_test() -> int:
    sample = (
        "Task-ID: KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938\n"
        f"Project-Path: {KOL_PROJECT}\n"
        "Base-Branch: main\n"
        "Work-Branch: feature/chatgpt-issue-172\n"
        "Goal: Harden deployment safety self-test diagnostics\n"
        f"Required-Checks: {base.EXPECTED_REQUIRED_CHECKS}\n"
        "Scope-Files: scripts/check-deployment-env-selftest.mjs\n"
        "Publication-Contract-Version: 4\n"
        "Publication-Action: pull-request\n"
        "Publication-Source-Issue: 172\n"
        "Publication-Repository: stvelikiy-star/kol-travel-platform\n"
        "Publication-Allowed-Actions: code-edit, commit, pull-request, push, tests\n"
        "Publication-Forbidden-Actions: database-mutation, deployment, destructive-operations, merge, other-project-access, payment-activation, production-change, scope-widening, secrets, supabase-restore\n"
        "Publication-Contract-Digest: " + "a" * 64 + "\n"
        "Retry-Attempt: 1\n"
        f"Retry-Reason: {FIRST_RETRY_REASON}\n"
        "Retry-Previous-Failure-SHA256: " + "b" * 64 + "\n"
        "Retry-Evidence-Backup: /tmp/first-retry-evidence\n"
        "Failure-Reason: CODEX_STAGE01B_FAILED | CodexExecutionError: CODEX_STAGE01B_FAILED: "
        + base.EXPECTED_FAILURE_FRAGMENT
        + "\n"
    )
    validate_second_failure(sample, "KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938")
    print(SELF_TEST_MARKER)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument(
        "--state-root",
        default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)),
    )
    parser.add_argument("--task-id")
    parser.add_argument("--stale-canary-backup")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.task_id or not args.stale_canary_backup:
        parser.error("--task-id and --stale-canary-backup are required")

    root = Path(args.root).resolve()
    if root != DEFAULT_ROOT or not (root / "orchestrator").is_dir():
        raise StaleCanaryRetryError("second retry requires canonical Control Center root")
    runtime = initialize(args.state_root)
    base.validate_runtime_paused(runtime)
    with base.acquire_supervisor_lock(runtime):
        result = retry_task(
            runtime,
            args.task_id,
            Path(args.stale_canary_backup),
        )
    for key, value in result.items():
        print(f"{key}={value}")
    print("KOL_V4_STALE_CANARY_RETRY_PREPARED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (base.RetryError, StaleCanaryRetryError) as exc:
        print(f"KOL_V4_STALE_CANARY_RETRY_BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
