# AI PROF Task Queue

Статусы:
- `pending` — ожидает запуска Stage 01A;
- `active` — задача захвачена оркестратором (Stage 01A или Stage 01B);
- `review` — Stage 01A validation PASS, ожидает Stage 01B (Claude);
- `pending_codex` — Stage 01B Claude PASS, ожидает независимый Codex audit;
- `failed` — техническая ошибка;
- `blocked` — отсутствует доступ или обязательное условие;
- `completed` — Codex PASS и задача закрыта.

Оркестратор не выполняет merge или production deploy. Stage 01B запускает
только Claude Code для реализации в пределах уже провалидированной Stage 01A
задачи; Codex в Stage 01B не запускается.
