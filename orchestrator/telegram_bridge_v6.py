#!/usr/bin/env python3
"""V6 durable Telegram task-state notifications; no new task authority."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import telegram_bridge as legacy
import telegram_bridge_v2 as v2
import telegram_bridge_v5 as v5

STATE_PATH = legacy.STATE_DIR / "telegram-task-notified-v6.json"
MAX_STATE_BYTES = 512_000
MAX_NOTIFIED_TASKS = 5000
POLL_SECONDS = 30


class DeliveryStateError(RuntimeError):
    pass


def fingerprint(state: tuple[str, str, str]) -> str:
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_state(path: Path) -> dict[str, str] | None:
    if path.is_symlink():
        raise DeliveryStateError("unsafe notification-state symlink")
    try:
        if path.stat().st_size > MAX_STATE_BYTES:
            raise DeliveryStateError("notification state exceeds size limit")
        obj = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise DeliveryStateError("invalid notification state") from exc
    if not isinstance(obj, dict) or obj.get("version") != 1:
        raise DeliveryStateError("unsupported notification state")
    tasks = obj.get("tasks")
    if not isinstance(tasks, dict) or len(tasks) > MAX_NOTIFIED_TASKS:
        raise DeliveryStateError("invalid notification task map")
    if any(not isinstance(task, str) or not v2.TASK_ID_RE.fullmatch(task)
           or not isinstance(digest, str) or len(digest) != 64
           or any(c not in "0123456789abcdef" for c in digest)
           for task, digest in tasks.items()):
        raise DeliveryStateError("invalid notification fingerprint")
    return tasks


def write_state(path: Path, tasks: dict[str, str]) -> None:
    if path.is_symlink() or len(tasks) > MAX_NOTIFIED_TASKS:
        raise DeliveryStateError("unsafe notification-state destination")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".telegram-v6-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"version": 1, "tasks": dict(sorted(tasks.items()))},
                      output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def notify_once(client: legacy.TelegramClient, *, path: Path = STATE_PATH,
                snapshot: dict[str, tuple[str, str, str]] | None = None) -> int:
    """Only checkpoint successful sends; failed sends retry on next poll."""
    if snapshot is None:
        snapshot = v2.task_state_snapshot()
    state = read_state(path)
    if state is None:
        # Baseline at first activation: do not flood the owner with months
        # of old failures or replay an existing approved task.
        write_state(path, {task: fingerprint(status)
                           for task, status in snapshot.items()})
        return 0
    sent = 0
    for task, status in sorted(snapshot.items()):
        digest = fingerprint(status)
        if state.get(task) == digest:
            continue
        messages = v5.task_change_messages_v5({}, {task: status})
        if len(messages) != 1:
            raise DeliveryStateError("unexpected task notification count")
        client.send(messages[0])
        state[task] = digest
        write_state(path, state)
        sent += 1
    return sent


def watch_task_changes_v6(client: legacy.TelegramClient, *,
                          poll_seconds: int = POLL_SECONDS) -> None:
    while True:
        try:
            notify_once(client)
        except (OSError, ValueError, legacy.BridgeError, DeliveryStateError) as exc:
            logging.warning("durable task watcher retry: %s",
                            v2._sanitize(type(exc).__name__, 80))
        time.sleep(poll_seconds)


def main() -> int:
    # Preserve all existing owner checks, task scope restrictions and V3
    # terminal handling. Replace only V2's volatile task-change watcher.
    v2.watch_task_changes = watch_task_changes_v6
    return v5.main()


if __name__ == "__main__":
    raise SystemExit(main())
