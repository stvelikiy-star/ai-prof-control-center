# AK BERMET — Security Fix 001 — Report

## Project

- Repository: `/home/agent/projects/ak-bermet`
- Base branch: `feature/supabase-auth-operations`
- Working branch: `fix/supabase-auth-security`
- Base commit: `aaaeb91`
- Source task: `AK_BERMET_FIX_SECURITY_001.md`
- Source audit: `AK_BERMET_CODEX_AUDIT_001.md` (verdict: FAIL)
- No merge performed. No secrets added, printed, or committed.

## What was fixed

### 1. Recursive RLS around `has_role()` / `is_staff()` / `user_roles` / `roles` (Critical, C-01)

`public.has_role()` and `public.is_staff()` are now `security definer` functions with `set search_path = public`, instead of plain invoker-rights SQL functions. Because they are invoked from inside the RLS policies on `user_roles`/`roles` themselves, running as invoker meant their internal reads of `user_roles` were themselves subject to `user_roles`' RLS policies — which call the same functions again, causing infinite-recursion policy errors. `security definer` makes these internal reads run under the defining role's privileges (bypassing RLS on the underlying tables), breaking the cycle.

Additional hardening applied exactly as specified in the task:
- Both functions now explicitly check `auth.uid() is not null`.
- Both already excluded soft-deleted role assignments (`deleted_at is null`); unchanged.
- `execute` is revoked from `PUBLIC` on both functions.
- `execute` is granted only to `anon` and `authenticated` — not the full `PUBLIC` pseudo-role. `anon` is required (not just `authenticated`) because several Phase 1 policies (e.g. `properties_staff_read`, `room_units_staff_read`) omit a `to` clause and are therefore evaluated for every role, including `anon`, when combined via `OR` with an anon-facing public-read policy on the same table. Restricting execution to `authenticated` only would have broken anonymous public-catalog reads once this schema is wired to the public site — out of scope restrictions ("не менять публичный сайт") required preserving this.

File: `supabase/migrations/20260721000200_identity_and_roles.sql`

### 2. Profile self-reactivation (High, H-01)

The RLS policy `profiles_self_update` still scopes the *row* (`id = auth.uid()`), but that alone doesn't stop a self-update from touching every column, including `is_active`/`deleted_at`. Added column-level privilege restriction:

```sql
revoke update on public.profiles from authenticated;
grant update (full_name, phone) on public.profiles to authenticated;
```

An authenticated user's own session can now only change `full_name`/`phone` on their own profile row. `is_active`, `deleted_at`, and any other administrative field are no longer updatable through the self-update RLS/grant path at all (no admin-facing update policy was added, since building an admin employee-management path is out of this task's scope — that remains a service-role/future-RPC concern).

File: `supabase/migrations/20260721000800_rls_policies.sql`

### 3. History triggers under RLS (High, H-02)

`log_lead_status_change()` and `log_booking_status_change()` are now `security definer` trigger functions with `set search_path = public`, and `execute` is revoked from `PUBLIC`. Both history tables (`lead_status_history`, `booking_status_history`) have RLS enabled with select-only policies and no insert policy for any application role, so the previous invoker-rights triggers would fail every lead/booking insert or status change with an RLS violation. Running as the defining role bypasses that RLS for the trigger's own insert, while the tables remain impossible to insert into directly by any client role (trigger firing is not subject to `EXECUTE` privilege checks, unlike direct SQL calls).

Also corrected the now-inaccurate comment in the RLS migration that claimed the trigger ran under the invoking role's privileges.

Files:
- `supabase/migrations/20260721000400_customers_leads.sql`
- `supabase/migrations/20260721000500_booking_core.sql`
- `supabase/migrations/20260721000800_rls_policies.sql` (comment only)

### 4. Legacy manager PIN session (High, H-03; Low, L-02)

Removed entirely rather than "disabled by flag," per the task's requirement not to leave any fail-open path:

- Deleted `src/lib/manager-auth.ts` (FNV-1a hash, PIN verification, session-value creation).
- Deleted `src/app/api/manager/login/route.ts`, `src/app/api/manager/session/route.ts`, `src/app/api/manager/logout/route.ts`, `src/app/manager/login/page.tsx`.
- Rewrote `src/lib/manager-session.ts::isManagerAuthenticated()` to check **only** Supabase Auth + role membership (`owner`/`administrator`/`manager`) via `getCurrentStaff()`. No environment flag can bypass it.
- Rewrote `src/middleware.ts`: removed the `/manager/login` public bypass and the PIN-cookie branch entirely. `MANAGER_AUTH_ENABLED` (and the whole legacy env-var block) no longer exists or gates anything — if Supabase Auth is not configured, every staff route (`/manager`, `/housekeeping`, `/technician`) now redirects to login instead of opening. This directly closes L-02 (fail-open on missing/misconfigured env var) in addition to H-03 (forgeable PIN cookie).
- Updated `ManagerHeader.tsx`, `staff/login/page.tsx`, `manager/settings/page.tsx`, `manager/layout.tsx`, `require-role.ts`, `.env.example`, and `api/manager/status/route.ts` to remove all remaining references to the deleted PIN mechanism.

## Additional correction (task's "Additional correction" section)

- **Middleware now checks `profiles.is_active = true` and `profiles.deleted_at is null`** for all three staff areas (`/manager`, `/housekeeping`, `/technician`), not just inside `getCurrentStaff()` at the API/data layer (M-02). A deactivated or soft-deleted profile is redirected to `/staff/unauthorized` even with a still-valid Supabase session, regardless of route.
- **`revoke all on function ... from public;` added before every `grant execute ... to authenticated;`** in `supabase/migrations/20260722001600_operational_automation.sql` (M-03) for the 12 operational RPCs that previously granted only, without a preceding revoke: `fn_mark_notification_read`, `fn_assign_staff`, `fn_accept_cleaning_task`, `fn_start_cleaning_task`, `fn_complete_cleaning_task`, `fn_report_cleaning_problem`, `fn_accept_maintenance_request`, `fn_start_maintenance_request`, `fn_record_maintenance_work_log`, `fn_complete_maintenance_work`, `fn_close_maintenance_request`, `fn_record_room_inspection`. (`fn_transition_room_status`, `fn_notify_role`, `fn_notify_user` already had the revoke and were left unchanged.)

## Changed files

```
 .env.example                                                  |  23 ++--
 src/app/api/manager/login/route.ts                             (deleted)
 src/app/api/manager/logout/route.ts                            (deleted)
 src/app/api/manager/session/route.ts                           (deleted)
 src/app/api/manager/status/route.ts                            |   2 -
 src/app/manager/layout.tsx                                    |   6 +-
 src/app/manager/login/page.tsx                                 (deleted)
 src/app/manager/settings/page.tsx                              |   6 +-
 src/app/staff/login/page.tsx                                   |  11 --
 src/components/manager/ManagerHeader.tsx                       |   7 +-
 src/lib/auth/require-role.ts                                   |   9 +-
 src/lib/manager-auth.ts                                         (deleted)
 src/lib/manager-session.ts                                     |  26 ++---
 src/middleware.ts                                              |  60 +++++----
 supabase/migrations/20260721000200_identity_and_roles.sql      |  39 +++++--
 supabase/migrations/20260721000400_customers_leads.sql         |  16 ++-
 supabase/migrations/20260721000500_booking_core.sql            |  12 +-
 supabase/migrations/20260721000800_rls_policies.sql            |  22 ++--
 supabase/migrations/20260722001600_operational_automation.sql  |  16 +++
```

No files outside this list were touched. No pricing/business-rule files, public marketing pages, or housekeeping/technician workflow logic were modified.

## Checks run

| Check | Result |
|---|---|
| `git diff --check` | PASS — no whitespace errors |
| `npx tsc --noEmit` | PASS (0 errors, after clearing stale `.next/types` referencing deleted routes) |
| `npm run build` (Next.js production build) | PASS — compiled successfully, 41/41 static pages generated |
| `npx eslint .` | PASS — 0 problems |
| Static review of SQL migrations (paren/dollar-quote balance, structural diff) | PASS — all edits are balanced; one pre-existing 1-paren prose-comment imbalance in `20260722001600_operational_automation.sql` predates this branch and is unrelated to these edits (confirmed via `git show HEAD`) |
| Grep sweep for residual references to removed PIN mechanism (`manager-auth`, `MANAGER_ACCESS_PIN`, `isValidManagerSession`, `/manager/login`, etc.) | PASS — zero matches remaining in `src/` |

No live Supabase/PostgreSQL instance was available in this environment, so the RLS/SECURITY DEFINER changes were **not** exercised against a real database (no `supabase db reset` / role-matrix integration test was run). This mirrors the audit's own stated limitation ("no database migration was executed... SQL findings are based on static PostgreSQL/Supabase semantics"). See Residual risks.

## Residual risks

1. **SQL fixes are unverified against a live Postgres/Supabase instance.** The `security definer` + `search_path` + revoke/grant changes are correct per standard PostgreSQL/Supabase semantics, but the required role-matrix integration tests (anon, staff, manager, administrator, deactivated user, RPC calls by `anon`) described in the audit's Recommended Corrections item 9 were not run, since no local Supabase/PostgreSQL instance is available in this environment. This should be done before merge.
2. **No admin path exists yet to reactivate/deactivate a profile.** By design, this fix only removes the *self*-service reactivation hole; it does not add a replacement admin/owner update path for `is_active`/`deleted_at` (would be scope expansion). Whoever manages staff deactivation today (service-role script, Supabase dashboard) is unaffected, but there is currently no RLS-governed way for an `owner`/`administrator` to flip these fields through the app either — pre-existing, unchanged by this fix.
3. **M-01 (housekeeping/technician interfaces are placeholders) was intentionally left unaddressed** — the task explicitly excludes implementing full housekeeping/technician workflows now.
4. **L-01 (migrations not directly re-appliable)** was intentionally left unaddressed — out of this task's required-fixes list.
5. `npm audit`: 2 high vulnerabilities remain upstream through Next.js's optional `sharp` 0.34.5 dependency, as already documented in `AK_BERMET.md`; unchanged by this branch, and `npm audit fix --force` remains prohibited per team rules.

## Verdict

**PASS** (pending Codex independent review and a live-database role-matrix test before merge, per `TEAM_RULES.md`'s required cycle). All four release-blocking defects and the additional correction items from `AK_BERMET_FIX_SECURITY_001.md` are addressed; no scope expansion; no merge performed.
