#!/usr/bin/env python3
"""Russian-language presentation of AI PROF Telegram messages.

Only converts known interface messages and labels. Never translates arbitrary
log contents, identifiers, file paths or secrets.
"""
from __future__ import annotations

import re
import sys

HELP = """🤖 AI PROF — управление на русском

📊 Проверка системы:
/ai status — общее состояние
/ai health — здоровье сервера
/ai audit — подробный аудит
/ai queue — очередь заданий
/ai approvals — задачи, ожидающие решения
/ai blockers kol-travel-platform — блокировки KÖL

🔎 Подробности:
/ai task <ID> — карточка задачи
/ai logs <ID> — журнал задачи
/ai git kol-travel-platform status — Git

🛠 Создание задачи:
/ai task kol-travel-platform | Название | Что нужно сделать

🔒 Безопасность: команды не дают доступ к терминалу, секретам,
принудительной публикации, миграциям и развертыванию.
MARINA SMART и другие отключённые проекты не принимают задания."""

PHRASES = {
    "AI PROF — read-only operational audit": "📋 AI PROF — аудит системы (только чтение)",
    "AI PROF — owner approvals (read-only)": "📋 AI PROF — задачи на согласовании",
    "AI PROF approvals: no tasks awaiting owner decision.": "Нет задач, ожидающих решения владельца.",
    "AI PROF task update": "🔔 AI PROF — изменение статуса задачи",
    "AI PROF task": "📌 AI PROF — задача",
    "AI PROF logs": "📄 AI PROF — журнал задачи",
    "AI PROF queue": "📋 AI PROF — очередь заданий",
    "AI PROF health": "🩺 AI PROF — состояние сервера",
    "AI PROF status": "📊 AI PROF — состояние системы",
    "Read-only request must not create a code-change task. For AI PROF diagnostics use /ai audit. No task was created.": "Запрос только на проверку: задача на изменение кода не создана. Для диагностики используйте /ai audit.",
    "Review passed; owner approval or publication is still pending.": "✅ Проверка пройдена. Требуется решение владельца; публикации пока нет.",
    "State: awaiting owner decision (not published)": "Статус: ожидает решения владельца (не опубликовано)",
    "Mode: read-only; no code edits, deploy, database mutation or shell from chat.": "Режим: только проверка. Код, база данных и размещение не меняются.",
    "Verdict: review warnings and task results; a healthy service is not proof all tasks succeeded.": "Вывод: работающая служба не означает, что все задачи завершились успешно. Проверьте предупреждения.",
    "No arbitrary shell, no secret values, no reset --hard, no clean -fd,": "Нет доступа к терминалу, секретам и разрушительным командам,",
    "no force-push, no migration and no deploy from Telegram.": "нет принудительной публикации, миграций и развертывания из Telegram.",
    "Server health problem detected.": "⚠️ Обнаружена проблема на сервере.",
    "Server health recovered.": "✅ Сервер снова работает нормально.",
    "Backup failure detected.": "🚨 Ошибка резервного копирования.",
    "Backup service recovered.": "✅ Резервное копирование восстановлено.",
    "AI PROF AK BERMET task completed": "✅ AI PROF AK BERMET — задача завершена",
    "AI PROF AK BERMET task blocked": "⛔ AI PROF AK BERMET — задача заблокирована",
    "AI PROF AK BERMET task failed": "❌ AI PROF AK BERMET — ошибка задачи",
    "AI PROF AK BERMET task cancelled": "🚫 AI PROF AK BERMET — задача отменена",
}
LABELS = {
    "Task-ID": "ID задачи", "Project": "Проект", "Project path": "Папка проекта",
    "Audit": "Проверка", "State": "Статус", "Queue": "Очередь",
    "Goal": "Цель", "Reason": "Причина", "Result": "Результат",
    "File": "Файл", "Log": "Журнал", "Host": "Сервер", "Time": "Время",
    "Previous state": "Было", "Current state": "Сейчас",
    "Exit code": "Код завершения", "Mode": "Режим",
    "Heartbeat": "Состояние цикла", "Disk": "Диск", "Swap": "Подкачка",
    "Bridge": "Telegram-бот", "Control Center": "Центр управления",
    "Current branch": "Текущая ветка", "Release branch": "Релизная ветка",
    "Worktree": "Рабочие файлы", "Changed files": "Изменённые файлы",
    "Current step": "Текущий этап", "Current Task-ID": "Текущая задача",
    "Progress": "Прогресс", "Counts": "Количество",
    "Commands": "Команды", "Projects": "Проекты", "Recent": "Недавние задачи",
    "Most recently modified queue task": "Последняя изменённая задача",
    "Pending approval resolution": "Ожидает решения владельца",
    "n8n readiness": "Готовность n8n", "Worktree": "Рабочая копия",
    "Divergence": "Разница веток", "Upstream": "Удалённая ветка",
    "Terminal reason": "Причина завершения", "Log": "Журнал",
}
STATES = {
    "queued": "в очереди", "pending": "в очереди",
    "running": "выполняется", "active": "выполняется",
    "review": "на проверке", "correction": "на исправлении",
    "codex audit": "проверка Codex", "pending_codex": "ожидает Codex",
    "approved": "проверено, ждёт решения", "passed": "проверено",
    "completed": "завершено", "blocked": "заблокировано",
    "failed": "ошибка", "cancelled": "отменено",
    "unknown": "неизвестно", "not found": "не найдено",
    "none": "нет", "healthy (responding)": "работает",
    "disabled": "отключено", "clean": "без изменений", "DIRTY": "есть изменения",
    "PASS": "✅ УСПЕХ", "WARNING": "⚠️ ПРЕДУПРЕЖДЕНИЕ",
    "CRITICAL": "🚨 КРИТИЧНО", "RECOVERY": "✅ ВОССТАНОВЛЕНО",
    "FAIL": "❌ ОШИБКА", "FAILED": "❌ ОШИБКА",
    "NOT VERIFIED": "не подтверждено",
}
PREFIXES = {
    "Task created:": "✅ Задача создана:",
    "Task not created:": "❌ Задача не создана:",
    "Release preparation failed:": "❌ Подготовка релиза не удалась:",
    "Unknown project:": "Неизвестный проект:",
    "Task not found:": "Задача не найдена:",
    "Project repository unavailable:": "Репозиторий недоступен:",
    "No task log file found for": "Журнал задачи не найден:",
    "Invalid Task-ID.": "Неверный ID задачи.",
    "Read-only:": "Только просмотр:",
    "Owner review:": "На согласовании:",
    "Recent tasks:": "Последние задачи:",
    "Campaigns:": "Кампании:",
    "Git diagnostics (read-only):": "Проверка Git (только чтение):",
    "Release:": "Релиз:",
    "Safety:": "Безопасность:",
    "Tasks:": "Задачи:",
    "Core:": "Основные команды:",
}

def localize(message: str) -> str:
    """Translate fixed UI copy; preserve values, task IDs, timestamps and paths."""
    if not isinstance(message, str):
        raise TypeError("message must be a string")
    if message.startswith("AI PROF Telegram Control Plane V2") or message.startswith("AI PROF commands:"):
        return HELP
    result = []
    for line in message.split("\n"):
        if line in PHRASES:
            result.append(PHRASES[line])
            continue
        for source, target in PREFIXES.items():
            if line.startswith(source):
                line = target + line[len(source):]
                break
        match = re.match(r"^(\s*)([^:]{2,48}): (.*)$", line)
        if match:
            indent, key, value = match.groups()
            if key in LABELS:
                line = f"{indent}{LABELS[key]}: {STATES.get(value, value)}"
        # Translate only standalone state labels; never rewrite log body.
        if line.startswith("State: "):
            line = "Статус: " + STATES.get(line[7:], line[7:])
        if line in STATES:
            line = STATES[line]
        # Plain alert prefix from system notification sender.
        line = re.sub(r"^\[(WARNING|CRITICAL|RECOVERY|INFO|TEST)\] AI PROF",
                      lambda m: "[" + STATES.get(m.group(1), m.group(1)) + "] AI PROF", line)
        result.append(line)
    return "\n".join(result)

if __name__ == "__main__":
    sys.stdout.write(localize(sys.stdin.read()))
