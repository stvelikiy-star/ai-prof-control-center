# AK BERMET — AUTONOMOUS DELIVERY ROADMAP

Дата актуализации: 2026-08-17

## Priority A0 — Agent context truth

Goal:
- убрать устаревшие Claude/develop правила;
- закрепить `main` как source of truth;
- закрепить GPT → Codex → AI PROF рабочую модель;
- ограничить owner gates только реальными бизнес/production решениями.

Gate:
- agent package V3 consistent;
- нет противоречий между SYSTEM_INSTRUCTIONS / STATE / APPROVAL_MATRIX / ROADMAP.

## Priority A1 — Codex primary execution

Goal:
- AK BERMET code-task path использует Codex как основного технического исполнителя;
- Claude не вызывается для AK BERMET;
- независимый audit остаётся отдельным шагом.

Gate:
- contract tests подтверждают, что AK BERMET task не маршрутизируется в Claude runner;
- техническая задача выполняется Codex runner в отдельной ветке;
- required checks запускаются до audit.

## Priority A2 — Trusted AK BERMET publisher

Goal:
- после Stage 01B/01C PASS автоматически публиковать только approved AK BERMET branch;
- commit/push/PR строго по Scope-Files;
- base = `main`;
- без merge/deploy/database/secrets authority на первом этапе.

Gate:
- exact AK BERMET path/repository pinned;
- changed files находятся только в approved scope;
- commit/push/PR работает;
- owner checkout возвращается в clean `main`;
- production mutation = false.

## Priority A3 — Telegram E2E

Goal:
- владелец отправляет одну `/ai task` команду;
- AI PROF создаёт задачу;
- Codex выполняет;
- checks + audit проходят;
- publisher создаёт PR;
- Telegram возвращает финальный статус и PR.

Gate:
- один реальный PASS E2E без ручного SSH между стадиями.

## Priority A4 — Autonomous repair

Goal:
- технический FAIL автоматически переводится в bounded repair cycle;
- exact diagnostics передаются следующему Codex запуску;
- не более установленного max fix cycles;
- security/infrastructure/owner gates остаются BLOCKED.

Gate:
- один контролируемый FAIL → repair → PASS E2E;
- owner не участвует в обычном repair cycle.

## Priority A5 — Merge policy

Goal:
- определить и протестировать безопасный auto-merge только для низкорисковых технических PR AK BERMET;
- required CI + Codex PASS обязательны;
- business/production gates исключены.

Gate:
- отдельная reviewed policy;
- protected `main` не обходится;
- production deploy authority не добавляется.

До A0–A4 новые крупные продуктовые функции не являются приоритетом. После закрытия delivery loop он используется для оставшихся release, backup, визуальных и production-readiness задач AK BERMET.
