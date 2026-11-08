#!/usr/bin/env python3
"""Owner-only diagnostics adapter; preserves V4 authorization and all other routes."""
from __future__ import annotations
import re
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
    if remainder == "help":
        return client.send(v2.V2_HELP + "\nRead-only: /ai audit (system + queue snapshot)\n")
    command = legacy.parse_command(normalized)
    if (command is not None and command.name == "task"
        and is_readonly_intent(command.title, command.instructions)):
        return client.send(
            "Read-only request must not create a code-change task. "
            "For AI PROF diagnostics use /ai audit. No task was created."
        )
    return _ORIGINAL_EXTENDED(update, config, client)

def main() -> int:
    v2.extended_handle_update = handle_update_v5
    v2.task_change_messages = task_change_messages_v5
    return v4.main()

if __name__ == "__main__":
    raise SystemExit(main())
