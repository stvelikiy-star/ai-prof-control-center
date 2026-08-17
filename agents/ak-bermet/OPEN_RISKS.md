# AK BERMET — OPEN RISKS

Дата актуализации: 2026-08-17

## R-01 — Production target identity
Severity: Critical for launch
Status: OPEN
DEV-проект подтверждён, но фактический production Supabase target должен быть явно идентифицирован до production backup/migration/deploy.

## R-02 — Production backup/restore evidence
Severity: High
Status: OPEN
DEV/preflight контур улучшен, но нужен валидированный backup + restore drill для фактического production target.

## R-03 — Autonomous publisher missing for AK BERMET
Severity: High for agent autonomy
Status: OPEN
Generic AK BERMET task profile пока не может автоматически commit/push/PR. Trusted publisher сейчас KÖL-only.

## R-04 — Executor routing may still reference Claude
Severity: High for agent autonomy
Status: OPEN
Project instructions уже переводятся на Codex-only, но runtime dispatch нужно проверить contract tests и реальным E2E.

## R-05 — Telegram delivery E2E not freshly evidenced
Severity: High for agent autonomy
Status: VERIFY
Bridge и Control Center ранее отвечали, но нужен свежий PASS: Telegram task → Codex → checks → audit → PR/status.

## R-06 — Repair E2E not freshly evidenced
Severity: Medium
Status: VERIFY
Auto-repair механизм существует, но нужен контролируемый FAIL → repair → PASS тест именно для AK BERMET Codex-only path.

## R-07 — Stale project documentation
Severity: Medium
Status: IN_PROGRESS
`agents/ak-bermet/*` и `ak-bermet/ai-system/CURRENT_STATE.md` содержали июльские Claude/develop/Google-Sheets-only утверждения. Agent package V3 исправляет Control Center side; project-side current state тоже должен быть обновлён отдельным безопасным PR.

## RESOLVED / NO LONGER OPEN

- 17 DEV staff accounts: provisioned and real-session UAT PASS.
- DEV staff role checks: 85 role checks + 8 policy checks PASS.
- Migration ledger/runtime security state: существенно продвинут и подтверждался после последних repair migrations; не считать это production evidence.
- Основные 3-day/G00 campaigns: ранее завершены; не использовать старые blocked записи как текущее состояние без свежего evidence.
