# AK BERMET

## Репозиторий

- Local: /home/agent/projects/ak-bermet
- GitHub: stvelikiy-star/ak-bermet
- Base branch: develop
- Production branch: main

## Текущий этап

Ветка:
feature/supabase-auth-operations

Коммит:
aaaeb91

Реализовано:
- Supabase/PostgreSQL migrations.
- Supabase Auth foundation.
- Staff roles and protected routes.
- Manager operations dashboard.
- Housekeeping interface.
- Technician interface.
- Operational cleaning and maintenance schema.
- RLS policies and reference seed data.

## Проверки

- ESLint: PASS.
- TypeScript: PASS.
- Next.js production build: PASS.
- Static generation: 44/44 PASS.
- Next.js: 15.5.21.
- npm audit: 2 high vulnerabilities remain upstream through Next.js optional sharp 0.34.5.
- npm audit fix --force is prohibited.

## Следующий безопасный этап

1. Независимый audit текущей feature-ветки.
2. Проверка Supabase migrations and RLS.
3. Проверка auth and role boundaries.
4. Проверка operational interfaces.
5. PASS/FAIL report.
6. Только после PASS — merge в develop.
