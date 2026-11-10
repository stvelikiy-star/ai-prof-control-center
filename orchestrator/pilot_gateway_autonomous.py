#!/usr/bin/env python3
"""Bounded, owner-authored second E2E through a persistent pilot gateway timer.

Only private repository issue #3 and its exact pinned body digest may trigger
the Codex worker. One active task, no retry of failed jobs, no merge/deployment.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_issue_e2e as core

ISSUE_NUMBER = 3
CONTRACT_SHA = "d2c2bd217bc842d1d5039e376afea3100657d83a9afcbb97cae9b8ce2b849d96"
PILOT_DIR = Path("/home/agent/projects/ai-prof-pilot-auto-gateway")
PILOT_STATE = Path("/home/agent/.local/state/ai-prof-control-center/reports/pilot-issue-3-e2e.json")
PILOT_LOCK = Path("/home/agent/.local/state/ai-prof-control-center/run/pilot-issue-3-e2e.lock")
TITLE = "[AI-PROF-PILOT] Add machine-readable task summary"
BRANCH = "feature/ai-prof-pilot-issue-3"
EXPECTED_BASE_SHA = "91e573c26468f3112d1dabf23e4420621241d914"
TEST_PYTHON = (
    "import json\n"
    "from scripts.task_health import evaluate_task, summarize_task_json\n"
    "cases = ["
    "{'task_id':'x','checks':[{'name':'ok','status':'PASS'}]},"
    "{'task_id':'x','checks':[{'name':'broken','status':'FAIL'}]},"
    "{'task_id':'x','checks':[{'name':'later','status':'PENDING'}]},"
    "{'task_id':'x','checks':[{'name':'broken','status':'FAIL'},"
    "{'name':'later','status':'PENDING'}]},"
    "{'task_id':'x','checks':[]}"
    "]\n"
    "for item in cases:\n"
    "    expected=json.dumps(evaluate_task(item),sort_keys=True,"
    "ensure_ascii=False,separators=(',',':'))\n"
    "    assert summarize_task_json(item)==expected, item\n"
    "try:\n"
    "    summarize_task_json({'task_id':'','checks':[]})\n"
    "except ValueError: pass\n"
    "else: raise AssertionError('invalid input must fail')\n"
)


def configure_core() -> None:
    core.ISSUE = ISSUE_NUMBER
    core.DIR = PILOT_DIR
    core.STATE = PILOT_STATE
    core.LOCK = PILOT_LOCK
    core.BRANCH = BRANCH
    core.MARKER = "AI-PROF-PILOT-E2E-V2"
    core.EXPECTED_TITLE = TITLE
    core.EXPECTED_BODY_SHA256 = CONTRACT_SHA
    core.INDEPENDENT_CHECKS = TEST_PYTHON
    core.PROMPT_SUFFIX = (
        "Only modify scripts/task_health.py and tests/test_task_health.py. "
        "Implement summarize_task_json using evaluate_task for validation. "
        "Add at least six new tests. Preserve existing API and tests. "
        "No commit/push/merge/deploy, no other projects, no secrets, no database. "
        "Run python3 -B -m unittest discover -s tests -p 'test_*.py' -q."
    )


def resource_gate() -> tuple[bool, str]:
    try:
        mem = dict(
            (line.split(':',1)[0], int(line.split(':',1)[1].strip().split()[0]))
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
            if line.startswith(("MemAvailable:", "SwapFree:"))
        )
        available = mem["MemAvailable"]
        import shutil
        usage = shutil.disk_usage("/")
        disk_percent = 100 * usage.used / max(1, usage.total)
        if available < 1500000:
            return False, "RAM_BELOW_1_5GB"
        if disk_percent >= 88:
            return False, "DISK_ABOVE_88_PERCENT"
        return True, "RESOURCE_PASS"
    except (OSError, ValueError, KeyError, IndexError):
        return False, "RESOURCE_CHECK_UNAVAILABLE"


def approved_issue() -> bool:
    issue = core.github(f"repos/{core.REPO}/issues/{ISSUE_NUMBER}")
    core.validate_issue(issue)
    if hashlib.sha256(issue["body"].encode()).hexdigest() != CONTRACT_SHA:
        raise core.PilotError("Contract body digest mismatch")
    return True


def run_once() -> int:
    configure_core()
    if PILOT_STATE.exists():
        state = json.loads(PILOT_STATE.read_text(encoding="utf-8"))
        if state.get("phase") == "passed":
            if state.get("notified") is not True:
                old = sys.argv
                try:
                    sys.argv = [old[0], "--notify-existing"]
                    return core.main()
                finally:
                    sys.argv = old
            print("PILOT_GATEWAY_ALREADY_COMPLETED_NO_DUPLICATE")
            return 0
        # A crashed or failed run is not automatically repeated. Preserve evidence.
        print("PILOT_GATEWAY_EXISTING_STATE_NO_REEXECUTION", state.get("phase"))
        return 0
    try:
        approved_issue()
    except (core.PilotError, OSError, ValueError, RuntimeError) as exc:
        print("PILOT_GATEWAY_CONTRACT_BLOCKED", type(exc).__name__)
        return 2
    allow, reason = resource_gate()
    if not allow:
        print("PILOT_GATEWAY_RESOURCE_DEFER", reason)
        return 0
    # Independent exact checkout guard before entrypoint checks a second time.
    if (core.git("rev-parse", "main") != EXPECTED_BASE_SHA
            or core.git("branch", "--show-current") != "develop"
            or core.git("status", "--porcelain=v1")):
        print("PILOT_GATEWAY_CHECKOUT_BLOCKED")
        return 2
    print("PILOT_GATEWAY_OWNER_TASK_ACCEPTED", ISSUE_NUMBER, flush=True)
    return core.main()


def main() -> int:
    if len(sys.argv) != 1:
        print("PILOT_GATEWAY_UNSUPPORTED_ARGUMENTS")
        return 2
    try:
        return run_once()
    except (OSError, ValueError, RuntimeError, IndexError) as exc:
        print("PILOT_GATEWAY_FAIL_CLOSED", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
