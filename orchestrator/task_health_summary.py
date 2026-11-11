#!/usr/bin/env python3
"""Read-only stage-aware queue health for the existing hourly shell check."""
from __future__ import annotations
import argparse
from pathlib import Path
import re

TASK_ID = re.compile(r"^[A-Z][A-Z0-9_-]{5,159}$")
PASS_MARKERS = {"01B": "STAGE_01B_CODEX_PASS", "01C": "STAGE_01C_AUDIT_PASS"}

def newest(queue: Path):
    try:
        paths = [p for p in queue.glob("*.md")
                 if p.is_file() and not p.is_symlink()
                 and TASK_ID.fullmatch(p.stem)]
        return max(paths, key=lambda p: p.stat().st_mtime_ns) if paths else None
    except OSError:
        return None

def verified_stage(root: Path, task_id: str, stage: str) -> bool:
    if not TASK_ID.fullmatch(task_id) or stage not in PASS_MARKERS:
        return False
    log_dir = root / "logs" / "orchestrator"
    try:
        logs = sorted(log_dir.glob(f"{task_id}-{stage}-*.log"),
                      key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for log in logs[:4]:
            if not log.is_file() or log.is_symlink() or log.stat().st_size > 150000:
                continue
            with log.open(encoding="utf-8") as f:
                if f.readline().strip() == PASS_MARKERS[stage]:
                    return True
    except (OSError, UnicodeError):
        return False
    return False

def summarize(root: Path) -> list[str]:
    queue = root / "queue"
    events = []
    for name in ("failed", "completed", "approved"):
        task = newest(queue / name)
        if task is None:
            continue
        events.append((task.stat().st_mtime_ns, name, task.stem))
    if not events:
        return ["UNKNOWN|no-verified-tasks"]
    events.sort(reverse=True)
    _, name, task_id = events[0]
    if name == "approved":
        if verified_stage(root, task_id, "01B") and verified_stage(root, task_id, "01C"):
            state = "REVIEW_PASS"
        else:
            state = "REVIEW_PENDING"
    elif name == "completed":
        state = "PASS"
    else:
        state = "FAILED"
    result = [f"{state}|{task_id}"]
    failures = [event for event in events if event[1] == "failed"]
    if failures and state != "FAILED":
        result.append(f"HISTORIC_FAILURE|{failures[0][2]}")
    return result

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state", type=Path, default=Path("/home/agent/.local/state/ai-prof-control-center"))
    args = p.parse_args()
    for entry in summarize(args.state):
        print(entry)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
