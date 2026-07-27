#!/usr/bin/env python3
"""Bounded supervisor for operations and Stage 01A -> Claude -> Codex."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_paths import DEFAULT_STATE_ROOT, initialize


DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
CHILD_TIMEOUT_SECONDS = 2100
HEARTBEAT_INTERVAL_SECONDS = 15
IDLE_INTERVAL_SECONDS = 5
MAX_BACKOFF_SECONDS = 300
SELF_TEST_MARKER = "CONTROL_LOOP_SELF_TEST_PASS"

SECRET_PATTERNS = (
    re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*=\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
)


@dataclass(frozen=True)
class ControlPaths:
    root: Path
    state: Path
    lock: Path
    pid: Path
    heartbeat: Path
    pause: Path
    stop: Path
    log: Path


def build_paths(root: Path, state_root: Path | str | None = None) -> ControlPaths:
    runtime = initialize(root if state_root is None else state_root)
    state = runtime / "run"
    logs = runtime / "logs" / "orchestrator"
    return ControlPaths(
        root=root,
        state=state,
        lock=state / "supervisor.lock",
        pid=state / "supervisor.pid",
        heartbeat=state / "heartbeat.json",
        pause=state / "paused",
        stop=state / "stop",
        log=logs / "control-loop.log",
    )


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_heartbeat(paths: ControlPaths, **updates) -> None:
    state = {
        "timestamp": utc_now(),
        "pid": os.getpid(),
        "state": "idle",
        "stage": None,
        "last_result": None,
        "consecutive_failures": 0,
    }
    try:
        loaded = json.loads(paths.heartbeat.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state.update(loaded)
    except (OSError, ValueError):
        pass
    state.update(updates)
    state["timestamp"] = utc_now()
    atomic_write(paths.heartbeat, json.dumps(state, sort_keys=True) + "\n")


def append_log(paths: ControlPaths, text: str) -> None:
    safe = redact(text)
    with paths.log.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()} {safe.rstrip()}\n")


def acquire_supervisor_lock(path: Path):
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise
    return handle


def child_commands(root: Path, runtime: Path) -> list[tuple[str, list[str]]]:
    python = sys.executable
    base = str(root)
    return [
        ("operations", [python, str(root / "orchestrator/operations_runner.py"), "--root", base, "--state-root", str(runtime)]),
        ("stage_01a", [python, str(root / "orchestrator/orchestrator.py"), "--root", base, "--state-root", str(runtime)]),
        ("claude", [python, str(root / "orchestrator/claude_runner.py"), "--root", base, "--state-root", str(runtime)]),
        ("codex", [python, str(root / "orchestrator/codex_runner.py"), "--root", base, "--state-root", str(runtime), "--once"]),
    ]


def run_process_with_heartbeat(
    argv: list[str], cwd: Path, timeout: int, heartbeat,
) -> subprocess.CompletedProcess:
    """Capture child output without pipe deadlock and refresh the heartbeat."""
    started = time.monotonic()
    last_heartbeat = started
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            argv, cwd=str(cwd), stdout=stdout_file, stderr=stderr_file,
            text=False, env=os.environ.copy(),
        )
        while process.poll() is None:
            now = time.monotonic()
            if now - started >= timeout:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise subprocess.TimeoutExpired(argv, timeout)
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                heartbeat()
                last_heartbeat = now
            time.sleep(min(1.0, max(0.05, timeout / 10)))
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", "replace")
        stderr = stderr_file.read().decode("utf-8", "replace")
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run_child(paths: ControlPaths, stage: str, argv: list[str], timeout: int) -> int:
    """Run exactly one foreground child with a finite timeout."""
    write_heartbeat(paths, state="running", stage=stage)
    append_log(paths, f"START stage={stage}")
    try:
        result = run_process_with_heartbeat(
            argv, paths.root, timeout,
            lambda: write_heartbeat(paths, state="running", stage=stage),
        )
        output = redact(f"{result.stdout or ''}\n{result.stderr or ''}").strip()
        append_log(paths, f"END stage={stage} returncode={result.returncode} output={output[:20000]}")
        return result.returncode
    except subprocess.TimeoutExpired as exc:
        append_log(paths, f"TIMEOUT stage={stage} timeout={timeout} detail={exc}")
        return 124
    except OSError as exc:
        append_log(paths, f"LAUNCH_FAILURE stage={stage} detail={exc}")
        return 126
    finally:
        write_heartbeat(paths, state="idle", stage=None)


def run_cycle(paths: ControlPaths, timeout: int = CHILD_TIMEOUT_SECONDS) -> int:
    """Run each stage once in fixed order; stop on infrastructure failure."""
    for stage, argv in child_commands(paths.root, paths.state.parent):
        if paths.stop.exists() or paths.pause.exists():
            break
        result = run_child(paths, stage, argv, timeout)
        if result not in (0, 1):
            write_heartbeat(paths, last_result=f"{stage}:{result}")
            return result
    write_heartbeat(paths, last_result="cycle_complete")
    return 0


def queue_counts(root: Path) -> dict[str, int]:
    names = (
        "pending", "active", "review", "pending_codex", "approved",
        "blocked", "failed", "completed",
    )
    return {
        name: len(list((root / "queue" / name).glob("*.md")))
        if (root / "queue" / name).is_dir() else 0
        for name in names
    }


def status(paths: ControlPaths) -> dict:
    heartbeat = {}
    try:
        heartbeat = json.loads(paths.heartbeat.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    running = False
    try:
        handle = acquire_supervisor_lock(paths.lock)
    except BlockingIOError:
        running = True
    else:
        handle.close()
    return {
        "running": running,
        "paused": paths.pause.exists(),
        "stop_requested": paths.stop.exists(),
        "heartbeat": heartbeat,
        "queues": queue_counts(paths.state.parent),
    }


def run_daemon(paths: ControlPaths, timeout: int, idle_interval: float) -> int:
    try:
        paths.stop.unlink()
    except FileNotFoundError:
        pass
    failures = 0
    atomic_write(paths.pid, f"{os.getpid()}\n")
    write_heartbeat(paths, state="idle", consecutive_failures=0)
    try:
        while not paths.stop.exists():
            if paths.pause.exists():
                write_heartbeat(paths, state="paused", stage=None)
                time.sleep(min(idle_interval, HEARTBEAT_INTERVAL_SECONDS))
                continue
            result = run_cycle(paths, timeout)
            if result == 0:
                failures = 0
                delay = idle_interval
            else:
                failures += 1
                delay = min(MAX_BACKOFF_SECONDS, max(idle_interval, 2 ** min(failures, 8)))
            write_heartbeat(paths, consecutive_failures=failures, backoff_seconds=delay)
            time.sleep(delay)
        write_heartbeat(paths, state="stopped", stage=None)
    finally:
        for marker in (paths.stop, paths.pid):
            try:
                marker.unlink()
            except FileNotFoundError:
                pass
    return 0


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-prof-control-loop-test-") as tmp:
        root = Path(tmp)
        paths = build_paths(root)
        atomic_write(paths.pause, "paused\n")
        if not status(paths)["paused"]:
            raise RuntimeError("SELF_TEST_PAUSE_FAILED")
        paths.pause.unlink()
        atomic_write(paths.heartbeat, '{"state":"test"}\n')
        if status(paths)["heartbeat"].get("state") != "test":
            raise RuntimeError("SELF_TEST_HEARTBEAT_FAILED")
        if redact("TOKEN=supersecret") != "[REDACTED]":
            raise RuntimeError("SELF_TEST_REDACTION_FAILED")
        if [name for name, _ in child_commands(root, paths.state.parent)] != ["operations", "stage_01a", "claude", "codex"]:
            raise RuntimeError("SELF_TEST_STAGE_ORDER_FAILED")
        first = acquire_supervisor_lock(paths.lock)
        try:
            try:
                acquire_supervisor_lock(paths.lock)
            except BlockingIOError:
                pass
            else:
                raise RuntimeError("SELF_TEST_LOCK_FAILED")
        finally:
            first.close()
    print(SELF_TEST_MARKER)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--once", action="store_true")
    actions.add_argument("--daemon", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--pause", action="store_true")
    actions.add_argument("--resume", action="store_true")
    actions.add_argument("--stop", action="store_true")
    actions.add_argument("--self-test", action="store_true")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--state-root", default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--child-timeout", type=int, default=CHILD_TIMEOUT_SECONDS)
    parser.add_argument("--idle-interval", type=float, default=IDLE_INTERVAL_SECONDS)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = build_paths(root, args.state_root)

    if args.self_test:
        return run_self_test()
    if args.status:
        result = status(paths)
        print(json.dumps(result, sort_keys=True) if args.json else json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.pause:
        atomic_write(paths.pause, f"{utc_now()}\n")
        print("CONTROL_LOOP_PAUSED")
        return 0
    if args.resume:
        try:
            paths.pause.unlink()
        except FileNotFoundError:
            pass
        print("CONTROL_LOOP_RESUMED")
        return 0
    if args.stop:
        atomic_write(paths.stop, f"{utc_now()}\n")
        print("CONTROL_LOOP_STOP_REQUESTED")
        return 0
    if args.child_timeout <= 0 or args.idle_interval <= 0:
        parser.error("timeouts and intervals must be positive")

    try:
        lock = acquire_supervisor_lock(paths.lock)
    except BlockingIOError:
        print("CONTROL_LOOP_ALREADY_RUNNING", file=sys.stderr)
        return 2
    with lock:
        if args.daemon:
            return run_daemon(paths, args.child_timeout, args.idle_interval)
        return run_cycle(paths, args.child_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
