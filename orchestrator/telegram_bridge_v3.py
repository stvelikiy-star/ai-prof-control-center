#!/usr/bin/env python3
"""AI PROF Telegram Control Plane V3 terminal-result delivery.

Keeps every V2 command and authorization rule unchanged. V3 only adds an
idempotent watcher for newly terminal AK BERMET tasks so the owner receives
the result without polling `/ai status` manually.

The bridge is the only component that reads the existing Telegram credential.
Publisher code remains secret-free and only writes queue/log evidence.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import telegram_bridge as legacy
import telegram_bridge_v2 as v2

STATE_ROOT = legacy.STATE_DIR
NOTIFIED_PATH = STATE_ROOT / "telegram-terminal-notified-v3.json"
AK_BERMET_PROJECT_PATH = "/home/agent/projects/ak-bermet"
TERMINAL_QUEUES = frozenset({"completed", "blocked", "failed", "cancelled"})
CODE_BRANCH_RE = re.compile(
    r"^(?:feature/chatgpt-issue-\d+|feature/telegram-[0-9a-f]{8}-[0-9a-f]{12})$"
)
PR_URL_RE = re.compile(
    r"(?m)^pr=(https://github\.com/stvelikiy-star/ak-bermet/pull/\d+)\s*$"
)


def _load_state(path: Path) -> dict[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        return None
    result: dict[str, str] = {}
    for task_id, queue in tasks.items():
        if (
            isinstance(task_id, str)
            and v2.TASK_ID_RE.fullmatch(task_id)
            and isinstance(queue, str)
            and queue in TERMINAL_QUEUES
        ):
            result[task_id] = queue
    return result


def _write_state(path: Path, tasks: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".terminal-notified-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "tasks": dict(sorted(tasks.items()))},
                handle,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _terminal_snapshot(state_root: Path) -> dict[str, tuple[str, Path, dict[str, str]]]:
    result: dict[str, tuple[str, Path, dict[str, str]]] = {}
    for task_id, locations in legacy._queue_locations(state_root).items():
        if len(locations) != 1:
            continue
        queue, path, fields = locations[0]
        if queue not in TERMINAL_QUEUES:
            continue
        if fields.get("Project-Path") != AK_BERMET_PROJECT_PATH:
            continue
        if not v2.TASK_ID_RE.fullmatch(task_id):
            continue
        result[task_id] = (queue, path, fields)
    return result


def _work_branch(task_path: Path) -> str:
    try:
        text = task_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return ""
    match = re.search(r"(?mi)^\s*Work-Branch:\s*([^\r\n]+)", text)
    return match.group(1).strip() if match else ""


def _publish_pr(state_root: Path, task_id: str) -> str:
    logs = state_root / "logs" / "orchestrator"
    try:
        candidates = sorted(
            logs.glob(f"{task_id}-PUBLISH-*.log"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return ""
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = PR_URL_RE.search(text)
        if match:
            return match.group(1)
    return ""


def _notification(
    state_root: Path,
    task_id: str,
    queue: str,
    task_path: Path,
    fields: dict[str, str],
) -> str | None:
    if queue == "completed":
        branch = _work_branch(task_path)
        pr_url = _publish_pr(state_root, task_id)
        # Code delivery is not terminal-success evidence until the publisher
        # log exists. Defer a few seconds instead of sending a premature PASS.
        if CODE_BRANCH_RE.fullmatch(branch) and not pr_url:
            return None
        lines = [
            "AI PROF AK BERMET task completed",
            f"Task-ID: {task_id}",
            "Result: PASS",
        ]
        if pr_url:
            lines.extend(
                [
                    f"PR: {pr_url}",
                    "Merge: not performed",
                    "Production: unchanged",
                ]
            )
        return legacy._telegram_truncate("\n".join(lines))

    reason = legacy._terminal_reason(queue, fields) or "no terminal reason recorded"
    label = {
        "blocked": "BLOCKED",
        "failed": "FAIL",
        "cancelled": "CANCELLED",
    }.get(queue, queue.upper())
    return legacy._telegram_truncate(
        "\n".join(
            [
                f"AI PROF AK BERMET task {queue}",
                f"Task-ID: {task_id}",
                f"Result: {label}",
                f"Reason: {legacy.redact(reason)[:500]}",
            ]
        )
    )


def notify_terminal_changes(
    client: legacy.TelegramClient,
    *,
    state_root: Path = STATE_ROOT,
    state_path: Path = NOTIFIED_PATH,
) -> int:
    """Send each new AK BERMET terminal transition exactly once."""
    snapshot = _terminal_snapshot(state_root)
    notified = _load_state(state_path)
    if notified is None:
        # First V3 start must not replay historical failures from July/August.
        _write_state(
            state_path,
            {task_id: queue for task_id, (queue, _path, _fields) in snapshot.items()},
        )
        return 0

    sent = 0
    for task_id, (queue, task_path, fields) in sorted(snapshot.items()):
        if notified.get(task_id) == queue:
            continue
        message = _notification(
            state_root,
            task_id,
            queue,
            task_path,
            fields,
        )
        if message is None:
            continue
        client.send(message)
        notified[task_id] = queue
        _write_state(state_path, notified)
        sent += 1
    return sent


def run_v3() -> None:
    """Legacy secure long-poll loop plus one bounded terminal-state scan."""
    config = legacy.load_config()
    client = legacy.TelegramClient(config)
    offset = legacy.read_offset()
    while True:
        try:
            updates = client.call("getUpdates", {"offset": offset, "timeout": 25})
            if not isinstance(updates, list):
                raise legacy.BridgeError("Telegram returned invalid updates")
            for update in updates:
                update_id = update.get("update_id") if isinstance(update, dict) else None
                if not isinstance(update_id, int):
                    continue
                # Preserve V1/V2 checkpoint-before-side-effect semantics.
                offset = max(offset, update_id + 1)
                legacy.write_offset(offset)
                legacy.handle_update(update, config, client)
            notify_terminal_changes(client)
        except legacy.BridgeError as exc:
            logging.error("bridge polling error: %s", exc)
            time.sleep(5)


def main() -> int:
    # V2 installs the extended owner-only command handler and then calls
    # legacy.main(). Replace only the long-poll function it will invoke.
    legacy.run = run_v3
    return v2.main()


if __name__ == "__main__":
    raise SystemExit(main())
