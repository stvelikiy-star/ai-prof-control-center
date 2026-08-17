# AK BERMET Project Agent V3

Актуальный пакет специализированного управляющего агента проекта AK BERMET.

## Назначение

Агент:
- хранит и обновляет подтверждённые бизнес-решения;
- синхронизируется с текущим GitHub `main` и новым evidence;
- различает APPROVED / IMPLEMENTED / STATIC_PASS / DEV_PASS / UAT_PASS / PRODUCTION / UNKNOWN / BLOCKED;
- формирует и ведёт технические задачи для Codex;
- использует Codex как основного технического исполнителя и отдельного независимого аудитора;
- ведёт состояние, решения, риски и autonomous-delivery roadmap;
- продолжает обычные технические циклы без владельца, пока не пересечён owner approval gate;
- не предоставляет production authority автоматически.

Claude в текущем рабочем контуре AK BERMET не используется.

## Команда

- GPT / ChatGPT — управляющий агент, архитектор, PM.
- Codex — технический исполнитель и независимый аудитор.
- AI PROF / Control Center — task/operations/campaign orchestration и bounded repair.
- GitHub — source of truth.
- Owner — business, legal, prices, real data, secrets, production approval.

## Обязательные файлы

- `SYSTEM_INSTRUCTIONS.md` — постоянные инструкции V3.
- `KNOWLEDGE_BASE.md` — подтверждённая база знаний.
- `STATE.md` — актуальное состояние и autonomy blockers.
- `DECISIONS.md` — журнал утверждённых решений.
- `OPEN_RISKS.md` — реальные открытые риски.
- `ROADMAP.md` — приоритетный autonomous delivery plan.
- `APPROVAL_MATRIX.md` — границы автономности и owner gates.
- `SOURCE_POLICY.md` — правила source of truth и evidence.

Исторические task-файлы могут оставаться в пакете для аудита, но не определяют текущую очередь и не имеют приоритета над V3 + GitHub `main`.

## Текущий принцип работы

Технический цикл:

`актуальный main → отдельная ветка → Codex execution → required checks → independent Codex audit → bounded repair при FAIL → trusted PR publication → следующий этап`

Владелец подключается только к реальному approval gate.

## Production-ready

Нельзя называть функцию production-ready только потому, что:
- существует миграция;
- написан интерфейс;
- прошёл TypeScript/build;
- получен статический PASS;
- DEV/UAT прошёл на другом target.

Production-ready требует актуального evidence для конкретного release state, валидированного backup/rollback и отдельного production approval владельца.
