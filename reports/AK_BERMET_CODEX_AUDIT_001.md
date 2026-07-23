# AK BERMET — Independent Read-Only Audit 001

Audit target:

- Repository: `/home/agent/projects/ak-bermet`
- Base: `develop`
- Branch: `feature/supabase-auth-operations`
- Commit: `aaaeb918c1df3f5d83f7053ff1f1f4fa4bc58cd8`
- Audit type: static, read-only
- Repository changes made: none

## 1. Executive summary

The branch is not safe to merge into `develop` in its current state.

The principal blocker is a recursive RLS design around `has_role()`, `is_staff()`, and `user_roles`. PostgreSQL will need to evaluate policies on `user_roles` while those helper functions themselves query `user_roles`. This can produce an infinite-recursion RLS error and break staff role resolution across middleware, protected pages, operational policies, and management RPC authorization.

Additional material issues:

- Staff can update every mutable field of their own profile, including `is_active` and `deleted_at`, allowing a deactivated user to reactivate their account.
- Lead and booking history triggers insert into RLS-protected history tables without an applicable insert policy or a safe `SECURITY DEFINER` implementation. Status-changing writes can therefore fail transactionally.
- The legacy manager cookie is only an unsalted 32-bit FNV-1a value derived from a likely low-entropy PIN and a known cookie name. It is forgeable offline.
- The housekeeping and technician “interfaces” are placeholders and expose no assigned operational work.
- Direct reapplication of the migration files is not idempotent, although normal Supabase migration tracking should prevent already-recorded migrations from being rerun.

Positive observations:

- Audit checkout, branch, and commit match the requested target.
- `git diff --check develop...feature/supabase-auth-operations` passes.
- No committed production secret was found by the static secret-pattern scan.
- The service-role credential remains referenced only through server-side environment access.
- The manager operations API performs a server-side authorization check before using the service-role client.
- Housekeeping and technician layouts include server-side role guards.
- Authentication uses `auth.getUser()` rather than trusting decoded cookie contents.
- Public operational-table access is not granted by the operational RLS migration.
- Seed data contains reference data rather than real customers, bookings, staff accounts, or credentials.

## 2. Critical findings

### C-01 — Recursive RLS role helpers can break all role-based authorization

- Severity: Critical
- Files:
  - `supabase/migrations/20260721000200_identity_and_roles.sql:84`
  - `supabase/migrations/20260721000200_identity_and_roles.sql:105`
  - `supabase/migrations/20260721000800_rls_policies.sql:61`
  - `supabase/migrations/20260721000800_rls_policies.sql:67`
- Exact reason:
  - `public.has_role()` queries `public.user_roles`.
  - `public.is_staff()` also queries `public.user_roles`.
  - RLS is then enabled on `user_roles`.
  - The `user_roles` policies invoke `public.has_role()` again.
  - The `roles` select policy invokes `public.is_staff()`, which also returns to `user_roles`.
  - These functions are ordinary invoker functions, so their reads remain subject to the caller’s RLS policies.
- Realistic impact:
  - PostgreSQL can reject role queries with an infinite-recursion policy error.
  - Middleware role loading can return no usable roles or fail.
  - Supabase staff authentication may succeed while all protected staff areas remain inaccessible.
  - Nearly every role-based policy and management RPC can become unusable.
  - The manager operations API can fail Supabase-role authorization unless the legacy PIN path is used.
- Recommended fix:
  - Replace the helpers with narrowly scoped `SECURITY DEFINER` functions owned by a controlled non-login role, with a fixed `search_path`.
  - Revoke default execution from `PUBLIC`, then grant only the required execution to `authenticated`.
  - Ensure the functions explicitly require a non-null `auth.uid()` and ignore deleted roles.
  - Alternatively, source roles from verified JWT claims maintained by a trusted hook, avoiding self-referential table-policy access.
  - Test as `anon`, an ordinary authenticated staff member, manager, administrator, and deactivated user against a real local Supabase/PostgreSQL instance.

## 3. High findings

### H-01 — A deactivated employee can reactivate their own profile

- Severity: High
- Files:
  - `supabase/migrations/20260721000800_rls_policies.sql:55`
  - `src/lib/auth/current-staff.ts:37`
- Exact reason:
  - `profiles_self_update` authorizes an authenticated user to update their own entire row.
  - The policy constrains only `id = auth.uid()`; it does not protect `is_active`, `deleted_at`, or other administration-controlled columns.
  - Application authorization treats `is_active=false` as the account-deactivation boundary.
- Realistic impact:
  - A dismissed or suspended employee with a still-valid Supabase session can call the REST API directly and set `is_active=true` and `deleted_at=null`.
  - Once reactivated, their existing role assignments restore access to staff and operational data.
- Recommended fix:
  - Do not grant broad row-level self-update on `profiles`.
  - Use column-level grants permitting self-update only for approved fields such as `full_name` and `phone`, or expose a narrow RPC that updates only those fields.
  - Reserve `is_active` and `deleted_at` for owner/administrator operations.
  - On deactivation, revoke refresh tokens/sessions and retire applicable role assignments.

### H-02 — Lead and booking status writes can fail because history triggers cannot pass RLS

- Severity: High
- Files:
  - `supabase/migrations/20260721000400_customers_leads.sql:106`
  - `supabase/migrations/20260721000500_booking_core.sql:107`
  - `supabase/migrations/20260721000800_rls_policies.sql:198`
  - `supabase/migrations/20260721000800_rls_policies.sql:222`
- Exact reason:
  - `log_lead_status_change()` and `log_booking_status_change()` are invoker trigger functions.
  - Their history tables have RLS enabled and only select policies.
  - Authorization to update the parent `leads` or `bookings` table does not confer insert permission on a separate history table.
  - The explanatory comment in the RLS migration is therefore incorrect.
- Realistic impact:
  - Inserting a lead or booking, or changing its status, can fail with an RLS violation when its trigger tries to create history.
  - The complete parent write is rolled back.
  - Existing lead-management workflows may regress once these database paths are adopted.
- Recommended fix:
  - Make the history trigger functions controlled `SECURITY DEFINER` functions with `set search_path = public`, revoke execution from `PUBLIC`, and keep history tables unavailable for direct client writes.
  - Or add a tightly constrained insert policy, though a protected trigger function is preferable for append-only audit integrity.
  - Add integration tests for lead insertion, booking insertion, and status transitions under each permitted application role.

### H-03 — Legacy manager sessions are forgeable and bypass Supabase role controls

- Severity: High
- Files:
  - `src/lib/manager-auth.ts:8`
  - `src/lib/manager-auth.ts:40`
  - `src/middleware.ts:38`
  - `src/lib/manager-session.ts:19`
  - `.env.example:44`
- Exact reason:
  - The manager cookie is `FNV-1a(PIN:cookie-name)`.
  - FNV-1a is a fast, unsalted, non-cryptographic 32-bit hash.
  - The cookie name is known and the configured PIN is expected to be low entropy.
  - The cookie contains no expiry or user identity protected by a server signature; expiry is only browser-side cookie metadata.
  - The same shared cookie grants manager access independently of Supabase roles.
- Realistic impact:
  - An attacker can enumerate common six-digit PINs offline, calculate the corresponding cookie, and send the forged value directly.
  - Compromise grants access to manager pages, lead APIs, and the service-role-backed operations endpoint.
  - Individual accountability and reliable revocation are unavailable.
- Recommended fix:
  - Remove the PIN fallback before production.
  - If temporary fallback is owner-approved, use a cryptographically random server-side session identifier stored in a server-side session store, or an HMAC-signed payload with user identity, issued-at time, expiry, and key rotation.
  - Rate-limit login attempts and audit successful and failed fallback access.
  - Never ship the example PIN as an effective runtime default.

## 4. Medium findings

### M-01 — Housekeeping and technician operational interfaces are not implemented

- Severity: Medium
- Files:
  - `src/app/housekeeping/page.tsx:1`
  - `src/app/technician/page.tsx:1`
- Exact reason:
  - Both pages only confirm role access.
  - Their own copy states that task and repair-request screens will appear at a later stage.
  - No API route or page logic loads assigned cleaning tasks, maintenance requests, work logs, attachments, or operational actions.
- Realistic impact:
  - Authenticated housekeeping and technician users cannot perform the operational workflows supported by the new schema and RPCs.
  - The project status describing these interfaces as implemented is materially overstated.
- Recommended fix:
  - Either narrow the declared scope/status to “protected placeholder routes,” or implement the assignment-scoped interfaces and API/server actions.
  - Verify accept, start, complete, report-problem, work-log, attachment, and inspection-result workflows with role-bound integration tests.

### M-02 — Middleware does not independently enforce profile activation

- Severity: Medium
- Files:
  - `src/middleware.ts:51`
  - `src/lib/auth/current-staff.ts:37`
- Exact reason:
  - Middleware validates the user and reads `user_roles`, but does not read `profiles.is_active` or `profiles.deleted_at`.
  - Activation is checked later by `getCurrentStaff()`, but `/manager` relies principally on middleware for page-level protection.
- Realistic impact:
  - A deactivated manager may still receive manager page HTML and static/mock content even if protected API calls later reject them.
  - Authorization behavior differs between routes and layouts, increasing the chance that future server components expose data before checking activation.
- Recommended fix:
  - Centralize the active-user check in the same server-side authorization primitive used by middleware and APIs.
  - Require `is_active=true` and `deleted_at is null` for all staff authorization.
  - Keep API/data-layer authorization even after middleware is corrected.

### M-03 — Security-definer RPC privileges are not explicitly revoked from `PUBLIC`

- Severity: Medium
- File: `supabase/migrations/20260722001600_operational_automation.sql`
- Exact reason:
  - PostgreSQL grants function execution to `PUBLIC` by default.
  - Public-facing RPC functions are granted to `authenticated`, but most are not first revoked from `PUBLIC`.
  - Several internal helpers are correctly revoked, demonstrating the intended posture is inconsistent.
- Realistic impact:
  - Anonymous callers can attempt to invoke RPCs that were described as authenticated-only.
  - Current assignment and role checks prevent most useful anonymous mutations, but the privilege boundary depends entirely on every function retaining a perfect internal check.
  - A future RPC or overload lacking one check would immediately be anonymously callable.
- Recommended fix:
  - Add `revoke all on function ... from public` for every application RPC before granting the minimum role.
  - Explicitly grant only to `authenticated` or `service_role` as appropriate.
  - Add privilege tests against `anon`.

## 5. Low findings

### L-01 — Migration scripts are not directly reapplication-safe

- Severity: Low
- Files: all files under `supabase/migrations/`
- Exact reason:
  - The migrations use unconditional `CREATE TYPE`, `CREATE TABLE`, `CREATE TRIGGER`, `CREATE POLICY`, and `ADD CONSTRAINT`.
  - Running the same SQL files directly a second time will fail on existing objects.
  - Reference seeds generally use conflict handling and are safer than the schema migrations.
- Realistic impact:
  - Manual replay, partial recovery, or an incorrectly reset migration-history table can fail and require operator intervention.
  - Under the normal Supabase migration ledger, already-applied migration versions should not be reapplied, so this is not by itself a production blocker.
- Recommended fix:
  - Document that migrations must only be applied through the migration ledger.
  - Test clean apply and database reset in CI.
  - For recovery-sensitive operations, use guarded procedural blocks or explicit precondition checks rather than broadly converting every migration to `IF NOT EXISTS`, which can conceal schema drift.

### L-02 — Manager auth disablement is fail-open

- Severity: Low, configuration-sensitive
- Files:
  - `src/middleware.ts:39`
  - `src/lib/manager-session.ts:20`
  - `.env.example:44`
- Exact reason:
  - `MANAGER_AUTH_ENABLED !== "true"` makes manager pages and manager APIs public.
  - This behavior predates much of the branch, but the new service-role-backed operations endpoint inherits it.
- Realistic impact:
  - A missing or mistyped production environment variable can expose manager operational data.
- Recommended fix:
  - Fail closed in production.
  - If demo mode genuinely requires public access, make it an explicit development-only condition and prevent application startup or deployment when production configuration is unsafe.

## 6. Files reviewed

Governing documents:

- `TEAM_RULES.md`
- `AK_BERMET.md`
- `AK_BERMET_CODEX_AUDIT_001.md`

Repository and configuration:

- Git state and complete `develop...feature/supabase-auth-operations` name/status/stat diff
- `package.json`
- `package-lock.json`
- `.env.example`
- `supabase/config.toml`
- `git diff --check`

Authentication and authorization:

- `src/middleware.ts`
- `src/lib/auth/current-staff.ts`
- `src/lib/auth/require-role.ts`
- `src/lib/manager-auth.ts`
- `src/lib/manager-session.ts`
- `src/lib/supabase-admin.ts`
- `src/lib/supabase/browser-client.ts`
- `src/lib/supabase/middleware-client.ts`
- `src/lib/supabase/server-client.ts`
- `src/types/auth.ts`
- `src/app/auth/callback/route.ts`
- `src/app/staff/login/page.tsx`
- `src/app/staff/unauthorized/page.tsx`
- `src/app/api/staff/logout/route.ts`
- Existing manager login, logout, session and protected API routes

Operational modules:

- `src/app/api/manager/operations/route.ts`
- `src/app/manager/operations/page.tsx`
- `src/app/manager/layout.tsx`
- `src/app/housekeeping/layout.tsx`
- `src/app/housekeeping/page.tsx`
- `src/app/technician/layout.tsx`
- `src/app/technician/page.tsx`
- `src/components/manager/OperationsFilters.tsx`
- `src/lib/operations-data.ts`
- `src/lib/operations-labels.ts`
- `src/types/operations.ts`

Migrations:

- `20260721000100_extensions_and_enums.sql`
- `20260721000200_identity_and_roles.sql`
- `20260721000300_inventory.sql`
- `20260721000400_customers_leads.sql`
- `20260721000500_booking_core.sql`
- `20260721000600_booking_integrity.sql`
- `20260721000700_audit_and_integrations.sql`
- `20260721000800_rls_policies.sql`
- `20260721000900_seed_reference_data.sql`
- `20260722001100_operational_enums.sql`
- `20260722001200_cleaning.sql`
- `20260722001300_maintenance.sql`
- `20260722001400_room_inspections.sql`
- `20260722001500_attachments_and_history.sql`
- `20260722001600_operational_automation.sql`
- `20260722001700_operational_rls.sql`

No full production build was repeated because the audit task explicitly said it had already passed and should not be repeated unless necessary. No database migration was executed, so SQL findings are based on static PostgreSQL/Supabase semantics and should be confirmed by role-based database integration tests after correction.

## 7. Recommended corrections

Required before merge:

1. Redesign `has_role()` and `is_staff()` so they do not recurse through `user_roles` RLS.
2. Restrict profile self-service updates and protect `is_active`/`deleted_at`.
3. Correct lead and booking history trigger execution under RLS.
4. Remove or cryptographically replace the legacy PIN-cookie mechanism.
5. Re-run clean database migration tests and role-matrix integration tests.

Strongly recommended:

6. Enforce active-profile status consistently in middleware, layouts, APIs, and data access.
7. Revoke RPC execution from `PUBLIC` before granting explicit roles.
8. Correct project reporting to identify housekeeping and technician pages as placeholders, or implement the promised operational interfaces.
9. Add tests covering:
   - anonymous access denial;
   - every staff role;
   - deactivated staff;
   - role removal;
   - cross-assignment access;
   - privilege escalation attempts;
   - status/history triggers;
   - operational RPC execution by `anon`;
   - service-role API access with missing and invalid sessions;
   - fail-closed production configuration.

## 8. Final verdict

# FAIL

The recursive RLS role design is a release-blocking authorization defect. The self-reactivation path, broken history-trigger permissions, and forgeable legacy manager session add independent high-severity reasons not to merge this commit into `develop` until corrected and verified against a real PostgreSQL/Supabase instance.