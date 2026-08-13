# KÖL SOURCE AND EVIDENCE POLICY

## Иерархия источников

1. Последнее явное решение владельца.
2. Текущий Git `main` KÖL и точный commit SHA.
3. Runtime evidence: build, auth/session, RLS/role-matrix, migration history, production/staging checks.
4. Актуальные recovery/master документы.
5. Исторические stage-отчёты.

## Правила

- Старый PASS не подтверждает новый commit.
- Static/build PASS не равен DB/runtime PASS.
- Seed/demo identity не считается рабочим real-auth аккаунтом.
- Наличие RLS не подтверждает корректность policy behavior.
- Наличие таблицы/route/page не подтверждает готовность бизнес-процесса.
- Persistent DB mutation подтверждается только фактическим migration/SQL evidence после backup gate.
- `UNKNOWN` остаётся `UNKNOWN`, пока нет доказательства.
- При drift между source и live schema фиксируй обе стороны, не выбирай одну молча.
- Секреты, пароли, токены, service-role values, cookies и приватные ключи не включать в отчёты, Git или задачи.
