#!/usr/bin/env python3
"""One pre-approved queue task. No general-purpose issue execution authority."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_issue_e2e as worker

ISSUE = 5
REPO = "stvelikiy-star/ai-prof-autonomy-pilot"
SHA = "dac6eac14ee6497f5a39464cfa31af30063e2f66a956565477aeca9f843d70d9"
ROOT = Path("/home/agent/projects/ai-prof-pilot-queue-issue5")
STATE = Path("/home/agent/.local/state/ai-prof-control-center/reports/pilot-issue-5-queue.json")
EXPECTED_BASE = "91e573c26468f3112d1dabf23e4420621241d914"
CHECKS = """from scripts.task_health import summarize_task_labels
ok = lambda items: {'task_id':'x','checks':items}
assert summarize_task_labels(ok([{'name':'ok','status':'PASS'}])) == 'READY'
assert summarize_task_labels(ok([{'name':'a','status':'FAIL'}])) == 'FAILED(a)'
assert summarize_task_labels(ok([{'name':'p','status':'PENDING'}])) == 'WAITING(p)'
assert summarize_task_labels(ok([{'name':'a','status':'FAIL'},{'name':'p','status':'PENDING'}])) == 'FAILED(a);WAITING(p)'
assert summarize_task_labels(ok([])) == 'EMPTY'
try:
    summarize_task_labels({'task_id':'','checks':[]})
except ValueError:
    pass
else:
    raise AssertionError('invalid state should be rejected')
"""


def configure() -> None:
    worker.ISSUE = ISSUE
    worker.DIR = ROOT
    worker.STATE = STATE
    worker.LOCK = STATE.parent.parent / "run/queue-pilot-issue5.lock"
    worker.BRANCH = "feature/ai-prof-pilot-issue-5"
    worker.MARKER = "AI-PROF-PILOT-E2E-V3"
    worker.EXPECTED_TITLE = "[AI-PROF-PILOT] Add deterministic task summary labels"
    worker.EXPECTED_BODY_SHA256 = SHA
    worker.INDEPENDENT_CHECKS = CHECKS
    worker.PROMPT_SUFFIX = (
        "Only modify scripts/task_health.py and tests/test_task_health.py. "
        "Implement summarize_task_labels using evaluate_task for input validation. "
        "Preserve all existing functions and tests. Add six test cases. "
        "No commit, no push, no deployment, no other project, no secrets."
    )


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    configure()
    issue = worker.github(f"repos/{REPO}/issues/{ISSUE}")
    worker.validate_issue(issue)
    if hashlib.sha256(issue["body"].encode()).hexdigest() != SHA:
        raise worker.PilotError("Issue body mismatch")
    if worker.git("rev-parse", "main") != EXPECTED_BASE:
        raise worker.PilotError("Unexpected base SHA")
    if STATE.exists():
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if data.get("phase") == "passed":
            if data.get("notified") is True:
                return 0
            sys.argv = [sys.argv[0], "--notify-existing"]
            return worker.main()
        raise worker.PilotError("Existing unfinished state: automatic rerun forbidden")
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
