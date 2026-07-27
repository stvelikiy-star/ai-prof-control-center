# AI PROF Task Schema V2

Обязательные поля:

- `Task-ID:`
- `Project-Path:`
- `Base-Branch:`
- `Work-Branch:`
- `Agent-Context:`
- `Goal:`
- `Scope:`
- `Out-of-Scope:`
- `Pass-Criteria:`
- `Required-Checks:`
- `Required-Commands:`
- `Required-Environment:`
- `Owner-Approval-Required: yes|no`

Правила:
- Work-Branch начинается только с `feature/` или `fix/`.
- Этап 01A не запускает Claude, не изменяет целевой проект, не делает merge/push/deploy.
- Он только валидирует задачу, доступы и контекст, затем безопасно перемещает её в `review`.

## Этап 01B (Claude runner)

- Обрабатывает только задачи из `queue/review`, провалидированные Stage 01A.
- Заново проверяет: чистоту проекта, Work-Branch, Base-Branch, доступ к `claude`.
- Создаёт/переключает Work-Branch и запускает Claude Code для реализации.
- Claude получает только: текст задачи, `SYSTEM_INSTRUCTIONS.md`, `SOURCE_POLICY.md`,
  `STATE.md`, `APPROVAL_MATRIX.md`, `DECISIONS.md`.
- Команды проверки (Required-Checks) описываются в задаче, но выполняются только
  из локального allowlist — сырой текст задачи никогда не исполняется как shell.
- Не делает merge/push/deploy и не запускает Codex.
- PASS → `queue/pending_codex`; ошибка Claude → `queue/failed`; отсутствие доступа
  или невалидная ветка → `queue/blocked`.
