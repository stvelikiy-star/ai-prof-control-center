# AI PROF Task Schema V2

Обязательные поля:

- `Task-ID:`
- `Execution-Mode: code|operations` (отсутствующее поле означает `code`)
- `Operation-Profile:` (точный ключ профиля или `none`)
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
- Режим по умолчанию — `code`; он сохраняет существующий Bubblewrap sandbox.
- `operations` выполняется отдельным runner только по точному локальному профилю;
  Goal/Instructions никогда не интерпретируются как команды.
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

## Этап 01C (Codex audit)

- Обрабатывает только `queue/pending_codex` в read-only sandbox.
- Принимает только точный первый непустой ответ `# PASS` или `# FAIL`.
- Проверяет неизменность Git и всего рабочего дерева до и после аудита.
- PASS → `queue/approved`; FAIL → `queue/review` с ограниченным счётчиком;
  ошибка инфраструктуры или протокола → `queue/blocked`.

## Production intake

`submit_task.py` создаёт тот же Task Schema V2 атомарно в `queue/pending`.
Реестр `projects.json` ограничивает проект, базовую/рабочую ветку и
`Scope-Files`; commit, push, merge и deployment запрещены.

Для зарегистрированной операции:

```text
--execution-mode operations
--operation-profile ak-bermet-supabase-rpc-deploy
```

Остальные аргументы intake остаются обязательными для совместимости схемы, но
не становятся командами операции.
