#!/usr/bin/env python3
"""Owner-only diagnostics adapter; preserves V4 authorization and all other routes."""
from __future__ import annotations
import re
import os
import json
import time
import tempfile
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
PRIVATE_ENABLED_MARKER = legacy.STATE_DIR / "run" / "owner-private-started"
OWNER_DRAFT_FILE = legacy.STATE_DIR / "run" / "owner-task-proposal-v1.json"
DRAFT_TTL_SECONDS = 86400
TASK_AREAS = {
    "телеграм": ("orchestrator/telegram_bridge.py", ("телеграм", "telegram")),
    "github": ("orchestrator/github_task_gateway.py", ("github", "гитхаб", "гитхабе")),
    "очередь": ("orchestrator/control_loop_service.py", ("очеред", "control center", "исполнител")),
}
NATURAL_CODE_VERBS = ("задача:", "задача ", "исправь ", "добавь ", "сделай ",
                      "реализуй ", "разработай ", "улучши ", "создай ")
MAX_NATURAL_TASK_CHARS = 3000


def _is_owner_private(message, config) -> bool:
    if not isinstance(message, dict):
        return False
    chat, sender = message.get("chat"), message.get("from")
    return (isinstance(chat, dict) and isinstance(sender, dict)
            and chat.get("type") == "private"
            and chat.get("id") == config.owner_user_id
            and sender.get("id") == config.owner_user_id)


def _owner_client(config):
    return legacy.TelegramClient(
        legacy.Config(config.token, config.owner_user_id, config.owner_user_id)
    )


def _enable_private_updates() -> None:
    """Durable opt-in after owner /start; no private history is stored."""
    marker = PRIVATE_ENABLED_MARKER
    marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if marker.is_symlink():
        raise legacy.BridgeError("unsafe private-notification marker")
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, b"owner-started\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def _owner_notification_client(group_client):
    class OwnerNotifier:
        def send(self, text):
            if PRIVATE_ENABLED_MARKER.is_file() and not PRIVATE_ENABLED_MARKER.is_symlink():
                try:
                    _owner_client(group_client.config).send(text)
                    return
                except legacy.BridgeError:
                    pass  # Telegram may revoke private messaging; retain group delivery.
            group_client.send(text)
    return OwnerNotifier()


def _save_proposal(instructions: str, scope: str | None) -> None:
    path = OWNER_DRAFT_FILE
    if path.is_symlink():
        raise legacy.BridgeError("unsafe owner proposal file")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"version": 1, "created": int(time.time()),
            "instructions": instructions, "scope": scope}
    blob = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if len(blob) > 5000:
        raise legacy.BridgeError("owner proposal too large")
    fd, name = tempfile.mkstemp(prefix=".owner-proposal-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(blob)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load_proposal():
    path = OWNER_DRAFT_FILE
    if path.is_symlink():
        return None
    try:
        if path.stat().st_size > 5000:
            return None
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if (not isinstance(obj, dict) or obj.get("version") != 1
            or type(obj.get("created")) is not int
            or not 0 <= time.time() - obj["created"] <= DRAFT_TTL_SECONDS
            or not isinstance(obj.get("instructions"), str)
            or not 12 <= len(obj["instructions"]) <= MAX_NATURAL_TASK_CHARS
            or obj.get("scope") is not None and not isinstance(obj.get("scope"), str)):
        return None
    return obj


def _clear_proposal() -> None:
    if not OWNER_DRAFT_FILE.is_symlink():
        OWNER_DRAFT_FILE.unlink(missing_ok=True)


def _area_choice(raw: str):
    lowered = raw.casefold()
    candidates = [(name, path) for name, (path, hints) in TASK_AREAS.items()
                  if any(hint in lowered for hint in hints)]
    if re.search(r"(?<!\w)бот(?:а|у|ом|е)?(?!\w)", lowered):
        if not any(name == "телеграм" for name, _ in candidates):
            candidates.append(("телеграм", TASK_AREAS["телеграм"][0]))
    return candidates[0] if len(candidates) == 1 else None


def _owner_proposal_reply(text: str, client) -> bool:
    simple = text.casefold().strip(" .!?\n")
    if simple in {"нет", "отмена", "отменить"}:
        if _load_proposal() is None:
            return False
        _clear_proposal()
        client.send("Поручение отменено. Можете написать новое: Задача: ...")
        return True
    proposal = _load_proposal()
    if not proposal:
        return False
    if simple in {"да", "подтверждаю", "запускай", "в работу"}:
        scope = proposal["scope"]
        if scope is None:
            client.send("Сначала выберите раздел: Telegram, GitHub или очередь.")
            return True
        raw = proposal["instructions"]
        _clear_proposal()  # At most one submission, even if Telegram retries.
        command = legacy.Command(
            "task", "ai-prof-control-center", raw[:legacy.TASK_TITLE_LIMIT],
            raw + "\nРазрешённый файл: " + scope, True,
        )
        try:
            projects = legacy.load_projects()
            _, project = legacy.resolve_project(command.project, projects)
            matches = v4.explicit_allowed_paths(command, project)
            if matches != [scope]:
                return client.send("Список разрешённых файлов изменился. Пришлите поручение заново.")
            task, project_id = legacy.submit(command, projects)
            client.send(
                f"✅ Принято: {task}\nПроект: {project_id}\nРаздел: {scope}\n"
                "Задание сохранено в очереди. Напишите «статус» или «задачи» "
                "для проверки; результат пришлю после независимого аудита."
            )
        except legacy.BridgeError as exc:
            client.send("Не удалось поставить задачу: " + legacy.redact(str(exc))[:250])
        return True
    aliases = {"telegram": "телеграм", "бот": "телеграм", "гитхаб": "github",
               "очереди": "очередь"}
    area = aliases.get(simple, simple)
    if area in TASK_AREAS:
        scope = TASK_AREAS[area][0]
        _save_proposal(proposal["instructions"], scope)
        client.send(f"Понял, раздел: {area} ({scope}). Запустить? Ответьте «Да» или «Нет».")
        return True
    return False

def _natural_task(text: str, config, client) -> None:
    """Use existing scoped, approved intake. Never grant broader file authority."""
    raw = text.strip()
    if raw.casefold().startswith("задача:"):
        raw = raw.split(":", 1)[1].strip()
    elif raw.casefold().startswith("задача "):
        raw = raw[7:].strip()
    if len(raw) < 12:
        return client.send("Опишите, что нужно сделать, одним сообщением: Задача: исправь ...")
    if len(raw) > MAX_NATURAL_TASK_CHARS:
        return client.send("Поручение слишком длинное. Разделите его на этапы до 3000 символов.")
    if is_readonly_intent(raw, raw):
        return client.send("Для проверки без изменений напишите: статус или аудит.")
    if legacy.redact(raw) != raw:
        return client.send("В поручении обнаружены конфиденциальные данные. Уберите их и отправьте заново.")
    if re.search(r"(?<!\w)(?:marina|марина|köl|кёл|ak-bermet|ак бермет)(?!\w)", raw.casefold()):
        return client.send("В простом режиме доступен только AI PROF. "
                           "Другой проект нужно назвать явно в /ai task и отдельно разрешить.")
    # Private natural tasks default ONLY to the owner's AI PROF project.
    # Other customer projects require explicit three-part /ai task authorization.
    command = legacy.Command("task", "ai-prof-control-center", raw[:legacy.TASK_TITLE_LIMIT], raw, True)
    try:
        projects = legacy.load_projects()
        _, project = legacy.resolve_project(command.project, projects)
        exact = v4.explicit_allowed_paths(command, project)
        if len(exact) != 1:
            suggested = _area_choice(raw)
            _save_proposal(raw, suggested[1] if suggested else None)
            if suggested:
                return client.send(
                    f"Понял задачу для AI PROF. Похоже, нужен раздел {suggested[0]} "
                    f"({suggested[1]}). Запустить? Ответьте «Да» или «Нет». "
                    "Поручение сохранено до завтра."
                )
            return client.send(
                "Поручение понял, но не определил раздел. Что менять? "
                "Ответьте одним словом: Telegram, GitHub или очередь. "
                "Затем попрошу подтвердить запуск. Поручение сохранено до завтра."
            )
        task_id, project_id = legacy.submit(command, projects)
        return client.send(
            f"✅ Задача принята: {task_id}\nПроект: {project_id}\n"
            f"Файл: {exact[0]}\nПоручение сохранено в очереди и не пропадёт "
            "при перезапуске.\nСтатус: напишите «задачи» или «статус». "
            "По завершении пришлю ссылку на Draft PR."
        )
    except legacy.BridgeError as exc:
        return client.send("Задача не создана: " + legacy.redact(str(exc))[:250])



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
    if any(word in text for word in (
        "ничего не измен", "без изменений", "только чтени", "read-only",
        "do not modify", "no changes",
    )):
        return True
    protected_subset = "не изменя" in text or "не трогай файлы" in text
    change_requested = bool(re.search(
        r"(?<!\w)(?:исправь|добавь|сделай|создай|разработай|реализуй|улучши)(?!\w)",
        text,
    ))
    return protected_subset and not change_requested

def handle_update_v5(update, config, client) -> None:
    if not isinstance(update, dict):
        return
    message = update.get("message")
    if not legacy.authorized(message, config):
        return
    private = _is_owner_private(message, config)
    if private:
        client = _owner_client(config)  # Reply in the same private conversation.
    raw = message.get("text") if isinstance(message, dict) else None
    if not isinstance(raw, str):
        return _ORIGINAL_EXTENDED(update, config, client)
    normalized = raw.strip()
    if private:
        simple = normalized.casefold().strip(" .!?\n")
        if normalized in ("/start", "/start@ai_prof_control_kg_bot"):
            try:
                _enable_private_updates()
            except FileExistsError:
                pass
            return client.send(
                "AI PROF готов ✅\nПишите обычным текстом:\n"
                "Задача: исправь ошибку в orchestrator/telegram_bridge.py\n\n"
                "Можете описать задачу подробно. Если неясно, где работать, "
                "я уточню раздел и попрошу подтвердить запуск.\n\n"
                "Статус: «статус». Список: «задачи». Справка: «помощь». "
                "Большие поручения выполняются последовательно; результат придёт сюда."
            )
        if simple in {"статус", "как дела", "состояние"}:
            return client.send(legacy.status_message(legacy.STATE_DIR, legacy.load_projects()))
        if simple in {"задачи", "мои задачи", "очередь"}:
            return client.send(v2.queue_message("ai-prof-control-center"))
        if simple in {"помощь", "как дать задачу", "команды"}:
            return client.send("Напишите: Задача: что исправить в каком файле. "
                               "Или /ai task <текст>. Статус: статус. Очередь: задачи.")
        if simple in {"аудит", "проверь сервер"}:
            return client.send(audit.build_report())
        if _owner_proposal_reply(normalized, client):
            return
        if normalized.casefold().startswith(NATURAL_CODE_VERBS):
            return _natural_task(normalized, config, client)
        if not normalized.startswith("/"):
            return client.send("Я готов принять задачу. Начните сообщение со слова "
                               "«Задача:» и напишите, что нужно сделать. "
                               "Для проверки напишите «статус».")
    # /task is a short command Telegram forwards in privacy-mode groups.
    if normalized.startswith("/task "):
        forwarded = dict(message)
        forwarded["text"] = "/ai task " + normalized[len("/task "):]
        update = dict(update)
        update["message"] = forwarded
        message = forwarded
        normalized = forwarded["text"]
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
        return client.send(
            "AI PROF — управление задачами\n"
            "Личный чат: /start, затем «Задача: исправь ...». "
            "Если раздел неясен, бот спросит и покажет предложение — ответьте «Да».\n"
            "Группа: /task <описание> или /ai task <описание>.\n"
            "Статус: /ai status; задачи: /ai queue; аудит: /ai audit.\n"
            "Большие поручения хранятся в очереди до завершения, "
            "но сложные изменения следует делить на проверяемые этапы. "
            "Изменения — только в разрешённых файлах; merge/deploy не автоматические."
        )
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
    def private_first_watch(client, *, poll_seconds=30):
        return watch_task_changes_v6(_owner_notification_client(client), poll_seconds=poll_seconds)
    v2.watch_task_changes = private_first_watch
    v2.extended_handle_update = handle_update_v5
    v2.task_change_messages = task_change_messages_v5
    return v4.main()

if __name__ == "__main__":
    raise SystemExit(main())
