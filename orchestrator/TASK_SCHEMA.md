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

## Universal Task Lifecycle Slice 1 (shadow model)

Slice 1 is an optional, side-effect-free projection over Task Schema V2. Legacy
task documents remain valid without lifecycle fields. Missing lifecycle data or
authority never implies permission: the shadow decision is `DENIED` / `BLOCK`.
Nothing in this section changes a queue, repository, runner, publisher, or owner
gate.

### States and outcomes

The backward-compatible state values are `pending`, `review`, `pending_codex`,
`approved`, `blocked`, and `failed`. `terminal` is the explicit terminal state.
Its required outcome is exactly one of `succeeded`, `failed`, `blocked`,
`cancelled`, or `superseded`. An outcome on a non-`terminal` state, or a
`terminal` state without an outcome, is invalid and must be blocked.

Legal forward transitions are:

```text
pending       -> review
review        -> pending_codex
pending_codex -> approved
pending_codex -> review          (bounded Stage 01C fix loop)
approved      -> terminal
```

Any non-terminal legacy state may also stop at `blocked` or `failed`. Those stop
states and `terminal` have no outgoing transitions. Same-state, skipped,
unknown, or otherwise unlisted transitions are illegal.

### Immutable bindings

A shadow snapshot binds all lifecycle evidence to the following immutable
identity fields:

- `task_id` — validated Task-ID;
- `project_id` — registered project identifier;
- `task_sha256` — SHA-256 of the validated task document;
- `source_sha` — exact 40- or 64-hex source revision used by the task.

Evidence entries contain `kind`, a 64-hex `sha256`, and the same `task_id`.
Identity/SHA mismatch, malformed SHA, unbound evidence, or attempted identity
replacement blocks the transition. Evidence required by the legal edges is:

```text
pending -> review:                 task_validated
review -> pending_codex:           implementation + required_checks
pending_codex -> approved:         stage_01c_pass
pending_codex -> review:           stage_01c_failure
approved -> terminal:              terminal_outcome
any live state -> blocked:         block_reason
any live state -> failed:          failure
```

Before evaluating or applying an edge, the source snapshot itself must contain
the minimum cumulative evidence required for its claimed state. Evidence newly
supplied for the proposed edge cannot retroactively repair an unsupported
source snapshot. For example, `review -> pending_codex` is blocked when the
`review` snapshot lacks `task_validated`, even if the call supplies all evidence
for the destination edge.

### Authority and fail-closed intersection

Authority is a three-value least-privilege lattice: `DENIED < OWNER_ONLY <
AUTONOMOUS`. Each legal edge names one action: `VALIDATE`, `IMPLEMENT`, `AUDIT`,
`FIX`, or `TERMINATE`. Repository/runtime actions additionally include `COMMIT`,
`PUSH`, `PUBLISH`, `PR`, and `MERGE` but Slice 1 performs none of them.

Effective authority is the intersection of every applicable layer. A missing
layer, missing action, malformed/unknown value, or any `DENIED` value resolves
to `DENIED`; `OWNER_ONLY` never satisfies an autonomous transition. In
particular, Slice 1 does not create publisher routes, and `PUBLISH`, `PR`, and
`MERGE` remain `OWNER_ONLY` or `DENIED` under the existing AI PROF profile.

### Determinism, retries, and reconciliation

Idempotency keys use canonical JSON and SHA-256 over only the schema version,
immutable task identity, source/target states, attempt number, and sorted unique
evidence bindings. Mutable time, host, process, queue, and filesystem values are
not inputs.

Retry classes are `NEVER`, `TRANSIENT`, `FIX_LOOP`, and
`OWNER_ACTION_REQUIRED`. Unknown failures are `NEVER`. Both fix attempts and
identical consecutive failures have explicit non-negative maxima; reaching
either limit stops retry. No unbounded default is valid.

Reconciliation returns only `NO_ACTION`, `ADVANCE_SHADOW`, `KEEP_SHADOW`,
`OWNER_ACTION_REQUIRED`, or `BLOCK`. It does not mutate either side. Identity
drift, non-adjacent drift, unknown actions, absent authority, or missing evidence
returns `BLOCK`.

Before comparing states, reconciliation verifies that each explicit snapshot
contains the minimum evidence for its claimed state. A `review` snapshot, for
example, is unsupported without `task_validated`, even when the observed state
is merely one edge behind. `KEEP_SHADOW` is returned only when the shadow's
forward edge from the observation has the matching action, autonomous effective
authority, and required transition evidence. Unsupported same-state snapshots
also return `BLOCK`; equality does not make missing evidence authoritative.

These rules preserve the architecture invariants E1–E7: immutable identity,
evidence-bound progress, least-authority intersection, legal monotonic state
changes, deterministic idempotency, finite retry/fix loops, and fail-closed
side-effect-free reconciliation.
