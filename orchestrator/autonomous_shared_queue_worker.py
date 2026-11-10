#!/usr/bin/env python3
"""AI PROF approved autonomous lane consumer; one fixed pilot profile only.

Shares authoritative gateway state and queue root, without touching historical
pending/active/approved records. All new issue contracts checked against
GitHub again before the Codex child gets any filesystem access.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway_autonomous_intake as intake
import github_task_gateway as gateway
import pilot_issue_e2e as core

RUN_LOCK = gateway.STATE_ROOT / "run" / "autonomous-shared-queue.lock"
PILOT_ROOT = Path("/home/agent/projects")
PILOT_SHA = "91e573c26468f3112d1dabf23e4420621241d914"
WORKER_TIMEOUT = 420
PROFILE_TITLE = "[AI-PROF-PILOT] Add deterministic task result counts"
PROFILE_MARKER = "AI-PROF-PILOT-E2E-V4"
INDEPENDENT = """from scripts.task_health import summarize_task_counts, evaluate_task
sample = lambda c: {'task_id':'test','checks':c}
assert summarize_task_counts(sample([{'name':'a','status':'PASS'}])) == {'passed':1,'failed':0,'pending':0,'ready':True}
assert summarize_task_counts(sample([{'name':'a','status':'FAIL'}])) == {'passed':0,'failed':1,'pending':0,'ready':False}
assert summarize_task_counts(sample([{'name':'a','status':'PENDING'}])) == {'passed':0,'failed':0,'pending':1,'ready':False}
assert summarize_task_counts(sample([{'name':'a','status':'FAIL'},{'name':'b','status':'PENDING'}])) == {'passed':0,'failed':1,'pending':1,'ready':False}
assert summarize_task_counts(sample([])) == {'passed':0,'failed':0,'pending':0,'ready':False}
try:
    summarize_task_counts({'task_id':'','checks':[]})
except ValueError:
    pass
else: raise AssertionError('invalid task accepted')
"""
INDEPENDENT_OVERVIEW = """from scripts.task_health import summarize_task_overview
item=lambda checks:{'task_id':'test','checks':checks}
assert summarize_task_overview(item([{'name':'a','status':'PASS'}]))=={'total':1,'passed':1,'failed':0,'pending':0,'status':'READY'}
assert summarize_task_overview(item([{'name':'a','status':'FAIL'}]))=={'total':1,'passed':0,'failed':1,'pending':0,'status':'FAILED'}
assert summarize_task_overview(item([{'name':'a','status':'PENDING'}]))=={'total':1,'passed':0,'failed':0,'pending':1,'status':'PENDING'}
assert summarize_task_overview(item([{'name':'a','status':'FAIL'},{'name':'b','status':'PENDING'}]))=={'total':2,'passed':0,'failed':1,'pending':1,'status':'FAILED'}
assert summarize_task_overview(item([]))=={'total':0,'passed':0,'failed':0,'pending':0,'status':'PENDING'}
try:
    summarize_task_overview({'task_id':'','checks':[]})
except ValueError: pass
else: raise AssertionError('invalid task accepted')
"""


INDEPENDENT_ATTENTION = """from scripts.task_health import summarize_task_attention
sample = lambda checks: {'task_id':'test','checks':checks}
assert summarize_task_attention(sample([{'name':'a','status':'PASS'}])) == {'ready':True,'failed_count':0,'pending_count':0,'needs_attention':False}
assert summarize_task_attention(sample([{'name':'a','status':'FAIL'}])) == {'ready':False,'failed_count':1,'pending_count':0,'needs_attention':True}
assert summarize_task_attention(sample([{'name':'a','status':'PENDING'}])) == {'ready':False,'failed_count':0,'pending_count':1,'needs_attention':True}
assert summarize_task_attention(sample([{'name':'a','status':'FAIL'},{'name':'b','status':'PENDING'}])) == {'ready':False,'failed_count':1,'pending_count':1,'needs_attention':True}
assert summarize_task_attention(sample([])) == {'ready':False,'failed_count':0,'pending_count':0,'needs_attention':True}
try: summarize_task_attention({'task_id':'','checks':[]})
except ValueError: pass
else: raise AssertionError('invalid task accepted')
"""


INDEPENDENT_REAL_QUEUE = """import tempfile
from pathlib import Path
from orchestrator.autonomous_queue_snapshot import summarize_queue
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    zeros={'pending':0,'active':0,'completed':0,'blocked':0}
    assert summarize_queue(root)==zeros
    (root/'autonomous_pending').mkdir()
    (root/'autonomous_pending'/'first.json').write_text('{}')
    assert summarize_queue(root)=={**zeros,'pending':1}
    (root/'autonomous_active').symlink_to(root/'autonomous_pending',target_is_directory=True)
    try: summarize_queue(root)
    except (ValueError,OSError): pass
    else: raise AssertionError('symlink directory not rejected')
"""


class ConsumerError(RuntimeError):
    pass


def get_issue(repo: str, number: int) -> dict:
    if repo not in (gateway.REPOSITORY, intake.PILOT_REPOSITORY):
        raise ConsumerError("Repo not allowlisted")
    return gateway.run_gh(["api", "-X", "GET", f"repos/{repo}/issues/{number}"], timeout=40)


def check_job(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > intake.MAX_RECORD_BYTES:
        raise ConsumerError("Unsafe queue entry")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConsumerError("Unreadable queue entry") from exc
    profile_cfg = intake.PROFILES.get(data.get("profile")) if isinstance(data, dict) else None
    if (not isinstance(data, dict)
            or set(data) != {"version", "kind", "main_issue", "profile",
                             "pilot_issue", "pilot_repository", "pilot_sha256",
                             "owner_issue_sha256", "deadline_seconds"}
            or data.get("version") != 1
            or data.get("kind") != intake.RECORD_MARKER
            or profile_cfg is None
            or data.get("pilot_repository") != profile_cfg.get("repository", intake.PILOT_REPOSITORY)
            or data.get("pilot_issue") != profile_cfg["pilot_issue"]
            or data.get("pilot_sha256") != profile_cfg["pilot_sha256"]
            or data.get("deadline_seconds") != 14400
            or not isinstance(data.get("main_issue"), int)
            or data["main_issue"] <= 0
            or not isinstance(data.get("owner_issue_sha256"), str)
            or len(data["owner_issue_sha256"]) != 64
            or path.name != f"AI_PROF_AUTO_MAIN_{data['main_issue']}.json"):
        raise ConsumerError("Queue record failed immutable schema")
    main = get_issue(gateway.REPOSITORY, data["main_issue"])
    trusted = intake.validate_contract(main)
    if trusted != data:
        raise ConsumerError("GitHub main issue no longer matches approved contract")
    pilot = get_issue(data["pilot_repository"], data["pilot_issue"])
    if (pilot.get("user", {}).get("login") != "stvelikiy-star"
            or pilot.get("state") != "open"
            or pilot.get("title") != profile_cfg["pilot_title"]
            or not isinstance(pilot.get("body"), str)
            or not pilot["body"].startswith(profile_cfg["pilot_marker"] + "\n")
            or hashlib.sha256(pilot["body"].encode()).hexdigest() != profile_cfg["pilot_sha256"]):
        raise ConsumerError("Pilot issue no longer matches reviewed contract")
    return data


def configure(data: dict) -> None:
    identifier = data["main_issue"]
    pilot_number = data["pilot_issue"]
    settings = intake.PROFILES.get(data["profile"])
    if settings is None or pilot_number != settings["pilot_issue"]:
        raise ConsumerError("Unknown approved profile")
    directory = PILOT_ROOT / f"ai-prof-auto-main-{identifier}-pilot-{pilot_number}"
    if directory.resolve(strict=False) != directory:
        raise ConsumerError("Pilot directory symlink")
    core.ISSUE = pilot_number
    core.DIR = directory
    core.STATE = gateway.STATE_ROOT / "reports" / f"autonomous-main-{identifier}-pilot-{pilot_number}.json"
    core.LOCK = gateway.STATE_ROOT / "run" / f"autonomous-main-{identifier}-pilot-{pilot_number}.lock"
    core.BRANCH = f"feature/ai-prof-auto-main-{identifier}-pilot-{pilot_number}"
    core.REPO = settings.get("repository", intake.PILOT_REPOSITORY)
    core.BASE = settings.get("base_branch", "main")
    core.SCOPE = frozenset(settings.get("scope", ("scripts/task_health.py", "tests/test_task_health.py")))
    core.MARKER = settings["pilot_marker"]
    core.EXPECTED_TITLE = settings["pilot_title"]
    core.EXPECTED_BODY_SHA256 = settings["pilot_sha256"]
    if data["profile"] == intake.PROFILE:
        core.INDEPENDENT_CHECKS = INDEPENDENT
        function_name = "summarize_task_counts"
    elif data["profile"] == intake.PROFILE_OVERVIEW:
        core.INDEPENDENT_CHECKS = INDEPENDENT_OVERVIEW
        function_name = "summarize_task_overview"
    elif data["profile"] == intake.PROFILE_ATTENTION:
        core.INDEPENDENT_CHECKS = INDEPENDENT_ATTENTION
        function_name = "summarize_task_attention"
    elif data["profile"] == intake.PROFILE_REAL_QUEUE:
        core.INDEPENDENT_CHECKS = INDEPENDENT_REAL_QUEUE
        function_name = "summarize_queue"
    else:
        raise ConsumerError("Unapproved profile")
    if data["profile"] == intake.PROFILE_REAL_QUEUE:
        core.PROMPT_SUFFIX = (
            "You may ONLY add or edit orchestrator/autonomous_queue_snapshot.py "
            "and tests/test_autonomous_queue_snapshot.py; implement summarize_queue(root) "
            "as a read-only deterministic scanner of four autonomous queue folders, "
            "reject symlinks and malformed filesystem entries, add at least eight tests. "
            "Do not touch secrets, network, state, services, production, "
            "commit, push, merge or deploy. Run Python unittest tests."
        )
        return
    core.PROMPT_SUFFIX = (
        "Only modify scripts/task_health.py and tests/test_task_health.py. "
        f"Implement {function_name} using evaluate_task. Add at least six "
        "unittest cases, preserve existing API. Never commit, push, deploy, "
        "read secrets, change other projects or access databases."
    )


def prepare_checkout(data: dict) -> None:
    configure(data)
    target = core.DIR
    if not target.exists():
        subprocess.run(
            ["git", "clone", "--branch", core.BASE,
             "git@github.com:" + core.REPO + ".git",
             str(target)],
            capture_output=True, text=True, timeout=90, check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        core.git("switch", "-c", "develop", core.BASE)
    if not (target / ".git").is_dir() or target.is_symlink():
        raise ConsumerError("Unsafe clone")
    baseline_sha = intake.PROFILES[data["profile"]].get("base_sha", PILOT_SHA)
    if (core.git("rev-parse", "HEAD") != baseline_sha
            or core.git("branch", "--show-current") != "develop"
            or core.git("status", "--porcelain=v1")
            or core.git("remote", "get-url", "origin") !=
            "git@github.com:" + core.REPO + ".git"):
        raise ConsumerError("Checkout fails baseline verification")


def verify_published(data: dict) -> bool:
    configure(data)
    if not core.STATE.is_file():
        return False
    try:
        state = json.loads(core.STATE.read_text(encoding="utf-8"))
        number = state.get("pull_request_number")
        commit = state.get("commit")
        if (state.get("phase") != "passed" or state.get("notified") is not True
                or state.get("issue") != data["pilot_issue"]
                or not isinstance(state.get("telegram_message_id"), int)
                or not isinstance(number, int) or number <= 0
                or not isinstance(commit, str) or len(commit) != 40
                or state.get("pull_request") !=
                f"https://github.com/{data['pilot_repository']}/pull/{number}"):
            return False
        result = gateway.run_gh([
            "api", "-X", "GET",
            f"repos/{data['pilot_repository']}/pulls/{number}"], timeout=40)
        files = gateway.run_gh([
            "api", "-X", "GET",
            f"repos/{data['pilot_repository']}/pulls/{number}/files"], timeout=40)
        return (
            result.get("state") == "open" and result.get("draft") is True
            and result.get("merged") is False
            and result.get("base", {}).get("ref") == core.BASE
            and result.get("head", {}).get("sha") == commit
            and result.get("head", {}).get("ref") == core.BRANCH
            and isinstance(files, list) and len(files) == 2
            and {x.get("filename") for x in files} == core.SCOPE
        )
    except (OSError, ValueError, KeyError, TypeError, gateway.GatewayError):
        return False


def relocate(source: Path, target_queue: str) -> Path:
    target = intake.queue_path(target_queue, int(source.stem.removeprefix("AI_PROF_AUTO_MAIN_")))
    if target.exists() or target.is_symlink():
        raise ConsumerError("Duplicate queue target")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source.replace(target)
    return target


def run_pilot_process(data: dict) -> int:
    worker = subprocess.Popen(
        ["/usr/bin/python3", str(Path(__file__).resolve()), "--worker",
         str(data["main_issue"])],
        start_new_session=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
             "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"},
    )
    try:
        worker.communicate(timeout=WORKER_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(worker.pid, signal.SIGKILL)
        worker.communicate()
        raise ConsumerError("Bounded child process timed out")
    return worker.returncode


def worker_once(number: int) -> int:
    path = intake.queue_path("autonomous_active", number)
    data = check_job(path)
    prepare_checkout(data)
    core.PRE_PUBLISH_CHECK = lambda: check_job(path)
    original_argv = sys.argv
    try:
        sys.argv = [str(Path(core.__file__).resolve())]
        return core.main()
    finally:
        sys.argv = original_argv
        core.PRE_PUBLISH_CHECK = None


def safe_codex_launch_gate(proc_root: Path = Path("/proc")) -> tuple[bool, str]:
    """Defer, never interrupt, if another Codex exec or memory pressure exists.

    Only inspect executable names and argument tokens; never log command text.
    Unknown /proc or PSI is a fail-closed defer for *new* tasks only.
    """
    import swap_pressure_gate as pressure
    try:
        values = {}
        for line in (proc_root / "meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith(("SwapTotal:", "SwapFree:", "MemTotal:", "MemAvailable:")):
                key, raw = line.split(":", 1)
                values[key] = int(raw.split()[0])
        total, free = values["SwapTotal"], values["SwapFree"]
        mem_total, mem_available = values["MemTotal"], values["MemAvailable"]
        if total < 0 or not 0 <= free <= total or mem_total <= 0 or not 0 <= mem_available <= mem_total:
            return False, "INVALID_MEMORY_METRICS"
        swap_pct = round(100 * (total - free) / total) if total else 0
        ram_pct = round(100 * mem_available / mem_total)
        psi = pressure.psi_some_avg60(proc_root / "pressure/memory")
        if pressure.severity(swap_pct, ram_pct, psi) == "CRITICAL":
            return False, "SWAP_MEMORY_PRESSURE"
        for entry in proc_root.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                argv = (entry / "cmdline").read_bytes().split(b"\0")
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue  # short-lived or inaccessible OS process
            if not argv or not argv[0]:
                continue
            if Path(argv[0].decode("utf-8", "replace")).name in ("codex", "codex-linux") and b"exec" in argv[1:]:
                return False, "OTHER_CODEX_ACTIVE"
        return True, "EXTERNAL_AGENTS_AND_PRESSURE_PASS"
    except (OSError, KeyError, ValueError, UnicodeError):
        return False, "EXTERNAL_RESOURCE_UNVERIFIED"


def tick() -> str:
    root = intake.QUEUE_ROOT
    active = sorted((root / "autonomous_active").glob("*.json"))
    if len(active) > 1:
        raise ConsumerError("More than one active task")
    if active:
        path = active[0]
        # Never re-run an interrupted Codex child. Validate GitHub/Telegram
        # evidence for completion; otherwise preserve as blocked.
        try:
            data = check_job(path)
            if verify_published(data):
                relocate(path, "autonomous_completed")
                return "RECOVERED_COMPLETED_WITH_REMOTE_EVIDENCE"
        except (ConsumerError, gateway.GatewayError):
            pass
        relocate(path, "autonomous_blocked")
        return "INTERRUPTED_BLOCKED_NO_AUTORETRY"
    pending = sorted((root / "autonomous_pending").glob("*.json"))
    if not pending:
        return "IDLE"
    path = pending[0]
    if path.is_symlink() or not path.is_file():
        raise ConsumerError("Invalid pending queue entry")
    if int(time.time()) - int(path.stat().st_mtime) > 14400:
        relocate(path, "autonomous_blocked")
        return "EXPIRED_BLOCKED"
    import sequential_job_dispatcher as baseline
    okay, reason = baseline.resource_gate()
    if not okay:
        return "RESOURCE_DEFERRED_" + reason
    allowed, reason = safe_codex_launch_gate()
    if not allowed:
        return "RESOURCE_DEFERRED_" + reason
    try:
        data = check_job(path)
    except (ConsumerError, gateway.GatewayError):
        relocate(path, "autonomous_blocked")
        return "INVALID_OWNER_CONTRACT_BLOCKED"
    active_path = relocate(path, "autonomous_active")
    try:
        code = run_pilot_process(data)
        if code == 0 and verify_published(data):
            relocate(active_path, "autonomous_completed")
            return "OWNER_ISSUE_COMPLETED_PR_TELEGRAM_PASS"
        relocate(active_path, "autonomous_blocked")
        return "WORKER_FAILED_BLOCKED"
    except (ConsumerError, OSError, ValueError, subprocess.TimeoutExpired, gateway.GatewayError):
        relocate(active_path, "autonomous_blocked")
        return "WORKER_INTERRUPTED_BLOCKED"


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        try:
            number = int(sys.argv[2])
            return worker_once(number)
        except (OSError, ValueError, ConsumerError, RuntimeError, gateway.GatewayError,
                subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return 1
    if len(sys.argv) != 1:
        return 2
    RUN_LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with RUN_LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("AI_PROF_AUTO_LANE_BUSY")
            return 0
        try:
            result = tick()
        except (ConsumerError, OSError, ValueError, gateway.GatewayError) as exc:
            print("AI_PROF_AUTO_LANE_FAIL_CLOSED", type(exc).__name__)
            return 1
        print("AI_PROF_AUTO_LANE_" + result, flush=True)
        return 1 if result.endswith("BLOCKED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
