#!/usr/bin/env python3
"""Durable sequential AI PROF job queue, initially bound to a private pilot.

Three immutable, reviewed job profiles: health -> owner issue #5 -> health.
No arbitrary commands, no project selection by an issue author, no auto-retries.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path("/home/agent/projects/ai-prof-control-center")
STATE = Path("/home/agent/.local/state/ai-prof-control-center/queue-dispatcher-v1.json")
LOCK = Path("/home/agent/.local/state/ai-prof-control-center/run/queue-dispatcher-v1.lock")
ISSUE_STATE = Path("/home/agent/.local/state/ai-prof-control-center/reports/pilot-issue-5-queue.json")
CMD = ["/usr/bin/python3", str(ROOT / "orchestrator/queue_pilot_issue5_worker.py")]
JOBS = (
    ("AI_PROF_QUEUE_V1_HEALTH_BEFORE", "health"),
    ("AI_PROF_QUEUE_V1_GITHUB_ISSUE_5", "pilot-issue-5"),
    ("AI_PROF_QUEUE_V1_HEALTH_AFTER", "health"),
)
TIME_BUDGET_SECONDS = 4 * 3600
WORKER_TIMEOUT_SECONDS = 420
ACTIVE_PHASES = frozenset(("running", "interrupted"))
TERMINAL_PHASES = frozenset(("completed", "blocked"))


class QueueError(RuntimeError):
    pass


def persist(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".queue-dispatch-", dir=STATE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o600)
        os.replace(name, STATE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def seed(now: int | None = None) -> dict:
    timestamp = int(time.time()) if now is None else now
    return {
        "version": 1, "created_at": timestamp,
        "deadline": timestamp + TIME_BUDGET_SECONDS,
        "jobs": [
            {"id": name, "kind": kind, "phase": "pending", "attempts": 0}
            for name, kind in JOBS
        ],
    }


def validate(data: object) -> dict:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise QueueError("Queue schema invalid")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != len(JOBS):
        raise QueueError("Queue count invalid")
    for item, (identity, kind) in zip(jobs, JOBS):
        if not isinstance(item, dict) or item.get("id") != identity or item.get("kind") != kind:
            raise QueueError("Queue contract changed")
        if item.get("phase") not in ("pending", "running", "interrupted", "completed", "blocked"):
            raise QueueError("Invalid job state")
        if not isinstance(item.get("attempts"), int) or item["attempts"] not in (0, 1):
            raise QueueError("Invalid attempts")
    if (not isinstance(data.get("deadline"), int)
            or not isinstance(data.get("created_at"), int)):
        raise QueueError("Missing deadline")
    return data


def resource_gate() -> tuple[bool, str]:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
        mem = next(int(s.split()[1]) for s in lines if s.startswith("MemAvailable:"))
        usage = shutil.disk_usage("/")
        if mem < 1500000:
            return False, "RAM_BELOW_1_5_GIB"
        if usage.used / usage.total >= 0.88:
            return False, "DISK_ABOVE_88_PERCENT"
        return True, "RESOURCES_PASS"
    except (OSError, ValueError, StopIteration):
        return False, "RESOURCES_UNVERIFIED"


def evidence_pass() -> tuple[bool, str]:
    try:
        data = json.loads(ISSUE_STATE.read_text(encoding="utf-8"))
        number = data.get("pull_request_number")
        commit = data.get("commit")
        if not (data.get("issue") == 5 and data.get("phase") == "passed"
                and data.get("independent_tests") == "PASS"
                and data.get("notified") is True
                and isinstance(data.get("telegram_message_id"), int)
                and isinstance(number, int) and number > 0
                and data.get("pull_request") ==
                f"https://github.com/stvelikiy-star/ai-prof-autonomy-pilot/pull/{number}"
                and isinstance(commit, str) and len(commit) == 40):
            return False, "ISSUE_5_EVIDENCE_MISSING"
        repo = "repos/stvelikiy-star/ai-prof-autonomy-pilot"
        pr_result = subprocess.run(
            ["/usr/bin/gh", "api", "-X", "GET", f"{repo}/pulls/{number}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        files_result = subprocess.run(
            ["/usr/bin/gh", "api", "-X", "GET", f"{repo}/pulls/{number}/files"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if pr_result.returncode or files_result.returncode:
            return False, "GITHUB_EVIDENCE_UNAVAILABLE"
        pr = json.loads(pr_result.stdout)
        files = json.loads(files_result.stdout)
        if (pr.get("draft") is True and pr.get("merged") is False
                and pr.get("state") == "open"
                and pr.get("base", {}).get("ref") == "main"
                and pr.get("head", {}).get("sha") == commit
                and isinstance(files, list) and len(files) == 2
                and {x.get("filename") for x in files} ==
                {"scripts/task_health.py", "tests/test_task_health.py"}):
            return True, "ISSUE_5_GITHUB_PR_AND_TELEGRAM_PASS"
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        pass
    return False, "ISSUE_5_REMOTE_EVIDENCE_MISMATCH"


def run_worker_bounded() -> int:
    # Codex is a child of the worker; kill the entire process group on timeout.
    # subprocess.run(timeout=...) alone can leave an orphaned Codex process.
    child = subprocess.Popen(
        CMD, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
             "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"},
    )
    try:
        child.communicate(timeout=WORKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        raise
    return child.returncode


def perform(job: dict) -> tuple[bool, str]:
    if job["kind"] == "health":
        status = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "ai-prof-control-center.service",
             "ai-prof-github-task-gateway.service", "ai-prof-telegram-bridge.service"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if status.returncode == 0 and status.stdout.splitlines() == ["active"] * 3:
            return True, "CORE_SERVICES_ACTIVE"
        return False, "CORE_SERVICES_NOT_HEALTHY"
    if job["kind"] == "pilot-issue-5":
        code = run_worker_bounded()
        if code != 0:
            return False, f"PILOT_WORKER_EXIT_{code}"
        return evidence_pass()
    return False, "UNREGISTERED_JOB_TYPE"


def progress(data: dict, *, now: int | None = None) -> str:
    validate(data)
    instant = int(time.time()) if now is None else now
    if any(x["phase"] == "blocked" for x in data["jobs"]):
        return "BLOCKED"
    if any(x["phase"] == "interrupted" for x in data["jobs"]):
        return "INTERRUPTED_NEEDS_RECONCILIATION"
    running = [x for x in data["jobs"] if x["phase"] == "running"]
    if running:
        # Unfinished at beginning of a fresh invocation means a prior process
        # crashed, was killed or timed out; fail closed until verified.
        task = running[0]
        if task["kind"] == "pilot-issue-5":
            ok, reason = evidence_pass()
            if ok:
                task.update(phase="completed", result="RECOVERED_" + reason,
                            finished_at=instant)
                persist(data)
                return "RECOVERED_COMPLETED"
        task.update(phase="interrupted", result="REQUIRES_MANUAL_RECONCILIATION",
                    finished_at=instant)
        persist(data)
        return "INTERRUPTED_NEEDS_RECONCILIATION"
    if all(x["phase"] == "completed" for x in data["jobs"]):
        return "ALL_JOBS_COMPLETED"
    if instant >= data["deadline"]:
        task = next(x for x in data["jobs"] if x["phase"] == "pending")
        task.update(phase="blocked", result="FOUR_HOUR_BUDGET_EXPIRED", finished_at=instant)
        persist(data)
        return "BLOCKED_TIMEOUT"
    allowed, reason = resource_gate()
    if not allowed:
        return "DEFERRED_" + reason
    task = next(x for x in data["jobs"] if x["phase"] == "pending")
    if task["attempts"]:
        task.update(phase="blocked", result="REPLAY_FORBIDDEN", finished_at=instant)
        persist(data)
        return "BLOCKED_REPLAY"
    task.update(phase="running", attempts=1, started_at=instant)
    persist(data)
    try:
        ok, result = perform(task)
    except subprocess.TimeoutExpired:
        ok, result = False, "WORKER_TIMEOUT"
    except (OSError, ValueError, QueueError, subprocess.SubprocessError):
        ok, result = False, "WORKER_EXCEPTION"
    task.update(phase="completed" if ok else "blocked",
                result=result, finished_at=int(time.time()))
    persist(data)
    return ("PASS_" if ok else "BLOCKED_") + task["id"]


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("QUEUE_BUSY_SINGLE_FLIGHT")
            return 0
        try:
            if not STATE.exists():
                data = seed()
                persist(data)
            else:
                data = validate(json.loads(STATE.read_text(encoding="utf-8")))
            result = progress(data)
        except (OSError, ValueError, QueueError) as exc:
            print("QUEUE_FAIL_CLOSED", type(exc).__name__)
            return 1
        print("AI_PROF_SEQUENCE_" + result, flush=True)
        return 1 if result.startswith(("BLOCKED", "INTERRUPTED")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
