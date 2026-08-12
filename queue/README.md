# AI PROF Task Queue

Этот каталог содержит только документацию. Живая очередь находится в
`/home/agent/.local/state/ai-prof-control-center/queue`; файлы задач никогда
не создаются и не перемещаются внутри Git worktree.

Статусы:
- `pending` — ожидает запуска Stage 01A;
- `active` — задача захвачена оркестратором (Stage 01A или Stage 01B);
- `review` — Stage 01A validation PASS, ожидает Stage 01B (Claude);
- `pending_codex` — Stage 01B Claude PASS, ожидает независимый Codex audit;
- `failed` — техническая ошибка;
- `blocked` — отсутствует доступ или обязательное условие;
- `approved` — Codex PASS, задача готова к решению владельца;
- `cancelled` — отменена владельцем до начала обработки;
- `completed` — устаревший совместимый статус, новые задачи сюда не направляются.

Оркестратор не выполняет merge или production deploy. Stage 01B запускает
только Claude Code для реализации в пределах уже провалидированной Stage 01A
задачи; Codex в Stage 01B не запускается.
