# AK BERMET — SOURCE AND EVIDENCE POLICY V3

Дата актуализации: 2026-08-17

## Иерархия источников

1. Последнее явно утверждённое решение владельца.
2. Текущий GitHub `main` AK BERMET и точный commit SHA.
3. Новые подтверждённые CI/runtime/DEV/UAT/production evidence для этого SHA или явно совместимого состояния.
4. `AK_BERMET_FINAL_MASTER_CONTEXT_V5_2026-08-08.md`, если доступен, как бизнесовая и архитектурная память на момент его создания.
5. `DECISIONS.md`, `STATE.md`, `OPEN_RISKS.md`, `ROADMAP.md`, `KNOWLEDGE_BASE.md`.
6. Исторические отчёты и старые ветки — только как история.

## Приоритет свежести

- Текущий `main` и новое доказательство всегда имеют приоритет над старым документом.
- Старый master context не может отменять более новый код или runtime evidence.
- `develop` не является source of truth по умолчанию.
- Если данные конфликтуют, агент фиксирует конфликт и получает доказательство, а не выбирает удобную версию.

## Правила evidence

- PASS старого commit не подтверждает новый commit.
- Static PASS не равен DEV/UAT/production PASS.
- Применённая миграция подтверждается migration ledger/API/SQL evidence.
- Наличие route/page не подтверждает полноценный workflow.
- Наличие таблиц не подтверждает корректность RLS.
- Mock/seed data не считается реальными данными.
- DEV Supabase нельзя называть production без явной идентификации target.
- Production preflight без network/write не является production deployment evidence.
- Google Sheets может быть переходным/операционным источником в конкретном контуре, но source-of-truth статус определяется только актуальной архитектурой и подтверждённым cutover.
- Любой `UNKNOWN` остаётся `UNKNOWN`, пока не получено доказательство.

## Agent/runtime evidence

Для автономного технического цикла доказательством считаются:

1. точная task branch/base;
2. changed-files внутри approved scope;
3. required checks PASS;
4. независимый Codex audit PASS или PASS_WITH_NON_BLOCKING_NOTES;
5. trusted publisher evidence для commit/push/PR;
6. terminal result в AI PROF/Telegram.

Ни один из этих шагов сам по себе не даёт production authority.

## Confidentiality

В отчётах, PR, Git, Telegram и task files запрещены:
- токены;
- пароли;
- API/service-role keys;
- cookie/session tokens;
- полные database URLs с credentials;
- полные персональные данные, если они не нужны для отдельно одобренной операции.
