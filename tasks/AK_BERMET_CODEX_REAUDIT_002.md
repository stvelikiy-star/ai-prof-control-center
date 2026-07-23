# AK BERMET — Codex Re-Audit 002

## Project

Repository:
/home/agent/projects/ak-bermet

Base branch:
feature/supabase-auth-operations

Fix branch:
fix/supabase-auth-security

Fix commit:
ae0c3e4

## Objective

Провести независимый read-only повторный аудит исправлений после AK_BERMET_CODEX_AUDIT_001.

## Verify

1. Recursive RLS полностью устранён.
2. has_role() и is_staff() безопасно реализованы через SECURITY DEFINER.
3. EXECUTE отозван у PUBLIC там, где требуется.
4. Пользователь не может сам менять profiles.is_active и profiles.deleted_at.
5. Триггеры истории лидов и бронирований безопасно работают под RLS.
6. Legacy FNV PIN-cookie полностью удалён.
7. Manager auth работает fail closed без Supabase.
8. Middleware проверяет active и non-deleted profile.
9. Manager, housekeeping и technician остаются защищены ролями.
10. Секреты не раскрыты.
11. Несвязанных изменений нет.

## Restrictions

- READ ONLY.
- Не менять файлы.
- Не создавать коммиты.
- Не устанавливать пакеты.
- Не переключать ветки.
- Не запускать npm audit fix.
- Не раскрывать секреты.

## Checks

- git diff feature/supabase-auth-operations...fix/supabase-auth-security
- git diff --check feature/supabase-auth-operations...fix/supabase-auth-security
- inspect changed TypeScript and SQL files
- inspect Claude implementation report
- full build не повторять без необходимости

## Report

Create:
/home/agent/projects/ai-prof-control-center/reports/AK_BERMET_CODEX_REAUDIT_002.md

Return:

1. Executive summary.
2. Remaining critical findings.
3. Remaining high findings.
4. Medium and low findings.
5. Verification of each prior blocking issue.
6. Residual risks.
7. Final verdict:
   - PASS
   - PASS WITH FIXES
   - FAIL
