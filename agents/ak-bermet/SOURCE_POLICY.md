# SOURCE AND EVIDENCE POLICY

## Иерархия

1. Решение владельца с датой.
2. Код и миграции точного commit SHA.
3. Codex audit этого SHA.
4. Staging runtime evidence.
5. UAT evidence.
6. Production evidence.
7. Сводные документы.
8. Исторические отчёты.

## Правила

- PASS старого commit не подтверждает новый commit.
- Static PASS не равен staging PASS.
- Применённая миграция подтверждается журналом миграций или SQL evidence.
- Наличие route/page не подтверждает полноценный workflow.
- Наличие таблиц не подтверждает корректность RLS.
- Mock/seed data не считается реальными данными.
- Google Sheets может быть переходным источником, но не должен конкурировать с Supabase после cutover.
- Любой `UNKNOWN` должен оставаться `UNKNOWN`, пока не получено доказательство.
- В отчётах запрещены токены, пароли, ключи, cookie и полные персональные данные.
