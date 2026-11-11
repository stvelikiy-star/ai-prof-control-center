#!/usr/bin/env python3
"""Owner-only diagnostics adapter; preserves V4 authorization and all other routes."""
from __future__ import annotations
import re
from pathlib import Path
import telegram_bridge as legacy
import telegram_bridge_v2 as v2
import telegram_bridge_v4 as v4
import ai_prof_readonly_audit as audit

_ORIGINAL_EXTENDED = v2.extended_handle_update
AUDIT_COMMAND = "/ai audit"
NATURAL_AUDIT_REQUESTS = frozenset({
    "проверь сервер", "аи проф проверь сервер", "ai prof проверь сервер",
    "проведи аудит аи проф", "покажи состояние системы",
})
_ORIGINAL_CHANGE_MESSAGES = v2.task_change_messages


def owner_approvals_report(state_root: Path = legacy.STATE_DIR) -> str:
    """Read-only owner review list; never imply publication authority."""
    projects = v2._projects()
    project_for_path = {str(p.get("path")): name for name, p in projects.items()}
    items = []
    locations = legacy._queue_locations(state_root)
    for task_id, entries in sorted(locations.items()):
        if not v2.TASK_ID_RE.fullmatch(task_id) or len(entries) != 1:
            continue
        queue, _path, fields = entries[0]
        if queue != "approved":
            continue
        project = project_for_path.get(fields.get("Project-Path"), "unknown")
        # Only read fixed task-specific log names; never interpolate instructions.
        evidence = []
        for stage, marker in (("01B", "STAGE_01B_CODEX_PASS"),
                              ("01C", "STAGE_01C_AUDIT_PASS")):
            candidates = sorted((state_root / "logs/orchestrator").glob(
                f"{task_id}-{stage}-*.log"))
            verified = False
            for path in candidates[-4:]:
                try:
                    if not path.is_file() or path.is_symlink() or path.stat().st_size > 150_000:
                        continue
                    if path.read_text(encoding="utf-8").startswith(marker + "\n"):
                        verified = True
                        break
                except (OSError, UnicodeError):
                    continue
            evidence.append(f"{stage}={'PASS' if verified else 'NOT VERIFIED'}")
        items.append(
            f"{task_id}\nProject: {v2._sanitize(project, 80)}\n"
            f"Audit: {', '.join(evidence)}\n"
            "State: awaiting owner decision (not published)"
        )
    if not items:
        return "AI PROF approvals: no tasks awaiting owner decision."
    tail = f"\n+{len(items)-10} more" if len(items) > 10 else ""
    return legacy._telegram_truncate(
        "AI PROF — owner approvals (read-only)\n\n"
        + "\n\n".join(items[:10]) + tail
    )


def task_change_messages_v5(before, after) -> list[str]:
    messages = _ORIGINAL_CHANGE_MESSAGES(before, after)
    return [message + ("\nReview passed; owner approval or publication is still pending."
                       if "State: passed" in message else "") for message in messages]

def is_readonly_intent(title: str, instructions: str) -> bool:
    """Fail closed on a read-only constraint, regardless of project."""
    text = (title + " " + instructions).casefold()
    return any(word in text for word in (
        "ничего не измен", "не изменя", "без изменений", "только чтени",
        "read-only", "do not modify", "no changes", "не трогай файлы",
    ))

def handle_update_v5(update, config, client) -> None:
    if not isinstance(update, dict):
        return
    message = update.get("message")
    if not legacy.authorized(message, config):
        return
    raw = message.get("text") if isinstance(message, dict) else None
    if not isinstance(raw, str):
        return _ORIGINAL_EXTENDED(update, config, client)
    normalized = raw.strip()
    prefix, _, remainder = normalized.partition(" ")
    if prefix.split("@", 1)[0] != "/ai":
        natural = re.sub(r"[.!?]+$", "", normalized.casefold()).strip()
        if natural in NATURAL_AUDIT_REQUESTS:
            return client.send(audit.build_report())
        return _ORIGINAL_EXTENDED(update, config, client)
    remainder = remainder.strip()
    if remainder == "audit":
        return client.send(audit.build_report())
    if remainder == "approvals":
        return client.send(owner_approvals_report())
    if remainder == "help":
        return client.send(v2.V2_HELP + "\nRead-only: /ai audit (system + queue snapshot)\n"
                           "Owner review: /ai approvals (no publication)\n")
    command = legacy.parse_command(normalized)
    if (command is not None and command.name == "task"
        and is_readonly_intent(command.title, command.instructions)):
        return client.send(
            "Read-only request must not create a code-change task. "
            "For AI PROF diagnostics use /ai audit. No task was created."
        )
    return _ORIGINAL_EXTENDED(update, config, client)

def main() -> int:
    # V6 replaces only the volatile V2 notification thread. Keeping this
    # entrypoint preserves the installed systemd unit and all owner-only
    # inbound command routing without changing privileged service config.
    from telegram_bridge_v6 import watch_task_changes_v6
    from telegram_ru import localize
    # Translation only at delivery: all legacy command handlers and V6 task
    # notifications preserve their existing authorization and checkpointing.
    old_send = legacy.TelegramClient.send
    def send_in_russian(client, message):
        return old_send(client, legacy._telegram_truncate(localize(message)))
    legacy.TelegramClient.send = send_in_russian
    v2.watch_task_changes = watch_task_changes_v6
    v2.extended_handle_update = handle_update_v5
    v2.task_change_messages = task_change_messages_v5
    return v4.main()

if __name__ == "__main__":
    raise SystemExit(main())
