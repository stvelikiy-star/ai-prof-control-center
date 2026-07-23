# AI PROF Task Queue

Статусы:
- `pending` — ожидает запуска;
- `active` — задача захвачена оркестратором;
- `review` — выполнение завершено, требуется Codex;
- `failed` — техническая ошибка;
- `blocked` — отсутствует доступ или обязательное условие;
- `completed` — Codex PASS и задача закрыта.

Оркестратор не выполняет merge или production deploy.
