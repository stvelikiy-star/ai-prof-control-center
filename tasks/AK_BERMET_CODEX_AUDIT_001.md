# AK BERMET — Codex Audit 001

## Project

Repository:
/home/agent/projects/ak-bermet

Base branch:
develop

Audit branch:
feature/supabase-auth-operations

Audit commit:
aaaeb91

## Objective

Провести независимый read-only аудит реализованных изменений Supabase Auth и operational staff modules.

## Scope

Проверить:

1. Supabase migrations:
   - порядок миграций;
   - внешние ключи;
   - enum;
   - индексы;
   - триггеры;
   - seed data;
   - совместимость повторного применения.

2. RLS and authorization:
   - manager;
   - housekeeping;
   - technician;
   - staff;
   - отсутствие публичного доступа;
   - отсутствие privilege escalation.

3. Next.js authentication:
   - middleware;
   - callback;
   - session handling;
   - role redirects;
   - protected routes;
   - logout.

4. Operational modules:
   - manager operations dashboard;
   - housekeeping interface;
   - technician interface;
   - API routes;
   - server/client boundary;
   - Supabase service-role usage.

5. Security:
   - leaked secrets;
   - unsafe environment handling;
   - service-role exposure;
   - insecure cookies;
   - authorization bypass;
   - SQL/RLS mistakes.

6. Regression risks:
   - existing manager login;
   - existing leads;
   - public website;
   - build compatibility.

## Restrictions

- READ-ONLY AUDIT.
- Do not edit files.
- Do not create commits.
- Do not switch branches.
- Do not run npm audit fix.
- Do not install packages.
- Do not change configuration.
- Do not expose secrets.

## Checks

Use existing repository state and inspect:

- git diff develop...feature/supabase-auth-operations
- git diff --check develop...feature/supabase-auth-operations
- relevant source files
- relevant SQL migrations
- package.json
- .env.example

Do not repeat full build unless strictly necessary because it already passed.

## Report format

Return:

1. Executive summary.
2. Critical findings.
3. High findings.
4. Medium findings.
5. Low findings.
6. Files reviewed.
7. Recommended corrections.
8. Final verdict:
   - PASS
   - PASS WITH FIXES
   - FAIL

For every finding include:
- severity;
- file;
- exact reason;
- realistic impact;
- recommended fix.
