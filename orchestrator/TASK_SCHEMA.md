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
- `Scope-Files:` (comma-separated paths constrained by the project profile)

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

`Base-Branch` выбирается только из `allowed_base_branches` зарегистрированного
проекта. `submit_task.py create --base-branch BRANCH` является необязательным;
без него используется `base_branch` профиля. Произвольные ветки отклоняются.

## Локальная integration campaign

Только профиль с `allow_local_campaign_merge: true` может разрешить отдельному
campaign controller локальные commit и `merge --no-ff`. Целевая ветка обязана
быть одновременно в `allowed_base_branches` и `local_integration_branches`.
Глобальные `allow_merge`, `allow_push` и `allow_deployment` остаются `false`.

Campaign-задача дополнительно содержит:

- `Campaign-ID:`
- `Integration-Branch:`
- `Local-Auto-Merge-Approved: yes`
- `Owner-Approval-Token:`

Controller обрабатывает только такую задачу из `queue/approved`, требует точное
совпадение сохранённого approval token и последний
`STAGE_01C_AUDIT_PASS`, проверяет Work-Branch и Scope-Files, затем выполняет
исключительно локальный commit/merge. Обычные задачи никогда автоматически не
merge-ятся. Push, remote operations, deployment, migrations, destructive SQL,
credentials и production data не входят в capability controller.

Для зарегистрированной операции:

```text
--execution-mode operations
--operation-profile ak-bermet-supabase-rpc-deploy
```

Остальные аргументы intake остаются обязательными для совместимости схемы, но
не становятся командами операции.
