# AK BERMET — Security Fix 001

## Project

Repository:
/home/agent/projects/ak-bermet

Base branch:
feature/supabase-auth-operations

Working branch:
fix/supabase-auth-security

## Objective

Исправить release-blocking дефекты, найденные в Codex Audit 001.

## Required fixes

### 1. Recursive RLS

Исправить recursive RLS вокруг:

- public.has_role()
- public.is_staff()
- public.user_roles
- public.roles

Требования:

- использовать безопасные SECURITY DEFINER helpers;
- установить фиксированный search_path;
- запретить выполнение PUBLIC;
- разрешить только необходимым ролям;
- проверять auth.uid() is not null;
- исключать удалённые и неактивные назначения;
- не допускать recursion через user_roles policies.

### 2. Profile self-reactivation

Запретить пользователю самостоятельно менять:

- is_active;
- deleted_at;
- административные поля профиля.

Самостоятельно разрешить менять только безопасные поля, например:

- full_name;
- phone.

### 3. History triggers under RLS

Исправить:

- log_lead_status_change();
- log_booking_status_change().

Требования:

- история должна записываться триггером;
- клиент не должен иметь прямой insert в history tables;
- использовать защищённые SECURITY DEFINER trigger functions;
- фиксированный search_path;
- revoke PUBLIC.

### 4. Legacy manager PIN session

Удалить или отключить небезопасный FNV-1a PIN-cookie fallback.

Требования:

- Supabase Auth становится основным manager auth;
- не оставлять публичный fail-open режим;
- production должен fail closed;
- MANAGER_AUTH_ENABLED не должен открывать manager routes при отсутствии переменной;
- не использовать общий PIN-cookie как production authentication.

## Additional correction

- middleware должен проверять profiles.is_active=true;
- middleware должен проверять profiles.deleted_at is null;
- application RPC functions должны делать REVOKE FROM PUBLIC перед GRANT.

## Restrictions

- Не менять публичный сайт.
- Не менять цены и бизнес-правила.
- Не реализовывать сейчас полноценные housekeeping/technician workflows.
- Не менять unrelated modules.
- Не выполнять merge.
- Не использовать npm audit fix --force.
- Не публиковать secrets.

## Required checks

- git diff --check
- npx tsc --noEmit
- npm run build
- статический анализ SQL migrations
- проверить отсутствие FNV manager-cookie authentication
- проверить fail-closed manager auth

## Required report

Создать:

/home/agent/projects/ai-prof-control-center/reports/AK_BERMET_SECURITY_FIX_001.md

Отчёт должен содержать:

- что исправлено;
- изменённые файлы;
- проверки;
- остаточные риски;
- verdict PASS или FAIL.
