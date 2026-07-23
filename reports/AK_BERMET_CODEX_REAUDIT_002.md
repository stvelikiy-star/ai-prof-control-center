# AK BERMET — Codex Re-Audit 002

## Audit target

- Repository: `/home/agent/projects/ak-bermet`
- Base branch: `feature/supabase-auth-operations`
- Fix branch: `fix/supabase-auth-security`
- Fix commit: `ae0c3e4faa5d71eed52215d46e98d01c246fc45d`
- Audit type: independent, static, read-only
- Repository changes made: none
- Branch switching, package installation, migration execution, and full build: not performed

## 1. Executive summary

Commit `ae0c3e4` correctly addresses the critical recursive-RLS defect and most findings from `AK_BERMET_CODEX_AUDIT_001`.

Confirmed corrections include:

- `has_role()` and `is_staff()` now use controlled `SECURITY DEFINER` execution with a fixed `search_path`.
- Their default `PUBLIC` execution privileges are revoked.
- Profile self-service updates are restricted to `full_name` and `phone`.
- Lead and booking history triggers can insert through RLS using hardened trigger functions.
- The legacy manager PIN/FNV cookie implementation and runtime endpoints are removed.
- Missing Supabase configuration now fails closed.
- Middleware checks profile activation and soft deletion.
- Manager, housekeeping, and technician routes retain role restrictions.
- Operational RPC execution is explicitly revoked from `PUBLIC` before being granted to `authenticated`.
- The changed-file set is limited to authentication, authorization, migrations, and related UI/configuration cleanup.
- `git diff --check` passes.
- No secret value was found in added lines.

However, one high-severity authorization defect remains: the shared server-side staff resolver checks `is_active` but does not check `profiles.deleted_at`. Manager API routes use this resolver directly and do not pass through page middleware. Consequently, a soft-deleted manager whose profile remains active and whose role assignment remains present can continue calling protected manager APIs, including the service-role-backed operations endpoint.

Because a meaningful account-revocation boundary remains bypassable, the fix is not ready for merge.

## 2. Remaining critical findings

None found.

The original critical recursive-RLS issue is statically resolved.

## 3. Remaining high findings

### H-04 — Soft-deleted managers remain authorized by direct manager API requests

- Severity: High
- Files:
  - [current-staff.ts](/home/agent/projects/ak-bermet/src/lib/auth/current-staff.ts:24)
  - [manager-session.ts](/home/agent/projects/ak-bermet/src/lib/manager-session.ts:12)
  - [operations route](/home/agent/projects/ak-bermet/src/app/api/manager/operations/route.ts:12)
  - [leads route](/home/agent/projects/ak-bermet/src/app/api/manager/leads/route.ts:10)
  - [lead detail route](/home/agent/projects/ak-bermet/src/app/api/manager/leads/[id]/route.ts:16)
  - [status route](/home/agent/projects/ak-bermet/src/app/api/manager/status/route.ts:10)

Exact reason:

- `getCurrentStaff()` selects only `full_name, is_active`; it does not load `deleted_at`.
- It rejects a profile only when `profile.is_active === false`.
- A row with `is_active=true` and non-null `deleted_at` is therefore returned as valid staff.
- `isManagerAuthenticated()` trusts that result and authorizes manager roles.
- Manager API handlers call `isManagerAuthenticated()` directly. Next.js middleware protects `/manager/:path*`, not `/api/manager/:path*`.
- Middleware’s correct `deleted_at is null` check therefore does not protect these APIs.

Realistic impact:

- A soft-deleted manager with a valid Supabase session and an undeleted role assignment can directly call manager APIs.
- This includes `/api/manager/operations`, which reads operational data using the server-side service-role client after the flawed authorization check.
- Soft deletion does not reliably revoke manager data access.

Required fix:

- Select `deleted_at` in `getCurrentStaff()`.
- Fail closed unless a profile exists, `is_active === true`, and `deleted_at === null`.
- Also reject profile and role query errors explicitly.
- Reuse this corrected resolver for all server-side role guards.
- Add a direct API test using a valid session belonging to a soft-deleted manager.

## 4. Medium and low findings

### M-01 — Housekeeping and technician pages remain placeholders

This prior medium finding is unchanged and was outside the security-fix scope.

The routes are role-protected, but they still do not provide the operational workflows represented by the new schema. Project status should continue to describe them as protected placeholders until the interfaces are implemented.

### L-01 — Migrations remain non-idempotent under direct replay

This prior low finding is unchanged and was outside scope. Normal migration-ledger execution mitigates it, but clean reset/apply testing remains advisable.

### L-03 — README still advertises the removed PIN login route

- File: [README.md](/home/agent/projects/ak-bermet/README.md:65)

The runtime PIN mechanism is removed, but the demo checklist still says:

- `/manager/login` — PIN login

The route no longer exists. This does not restore the security vulnerability, but it is stale operational documentation and can mislead testers or operators.

## 5. Verification of requested and prior blocking issues

| Verification item | Result | Assessment |
|---|---|---|
| 1. Recursive RLS completely eliminated | PASS, static | `has_role()` and `is_staff()` execute as definer and no longer evaluate `user_roles` as the invoking role. |
| 2. `has_role()` and `is_staff()` safely use `SECURITY DEFINER` | PASS, static | Both have `SECURITY DEFINER`, a fixed `search_path`, non-null `auth.uid()` checks, and ignore deleted role assignments. |
| 3. Required `PUBLIC` execution revoked | PASS | Revoked for both role helpers, both history trigger functions, and the audited operational RPCs before explicit grants. |
| 4. User cannot change `profiles.is_active` or `deleted_at` | PASS, static | Table-level update is revoked and authenticated self-update is granted only for `full_name` and `phone`. |
| 5. Lead/booking history triggers operate safely under RLS | PASS, static | Both trigger functions use `SECURITY DEFINER`, fixed `search_path`, and revoked public execution. Live database confirmation remains outstanding. |
| 6. Legacy FNV PIN cookie completely removed | PASS for runtime; documentation cleanup remains | Implementation module, endpoints, manager login page, cookie handling, environment variables, and call sites are removed. Historical reports and comments remain informational; README contains one stale instruction. |
| 7. Manager auth fails closed without Supabase | PASS | The legacy configuration bypass is gone. Missing Supabase clients result in failed authentication or login redirect. |
| 8. Middleware checks active, non-deleted profile | PASS | Middleware requires an existing profile with `is_active=true` and `deleted_at=null`. |
| 9. Manager, housekeeping, technician remain role-protected | PASS for pages | Middleware retains separate role allowlists; housekeeping and technician layouts also use server-side guards. Manager APIs have the soft-deletion defect described in H-04. |
| 10. Secrets not disclosed | PASS | No secret value was found in added diff lines. `.env.example` contains empty placeholders only. |
| 11. No unrelated changes | PASS | All 19 changed files relate to the audited security corrections and necessary UI/configuration cleanup. |

### Prior blockers

- C-01 recursive RLS: resolved.
- H-01 self-reactivation: resolved.
- H-02 history-trigger RLS failure: resolved statically.
- H-03 forgeable PIN cookie: resolved in runtime code.
- M-02 middleware activation enforcement: resolved.
- M-03 operational RPC `PUBLIC` execution: resolved.

A new high finding, H-04, prevents an overall pass.

## 6. Scope and checks performed

Confirmed repository state:

- `HEAD` is exactly `ae0c3e4faa5d71eed52215d46e98d01c246fc45d`.
- Current branch is `fix/supabase-auth-security`.
- Working tree is clean.
- `feature/supabase-auth-operations` is an ancestor of the fix commit.
- Fix commit contains 19 changed files: 148 insertions and 330 deletions.
- `git diff --check feature/supabase-auth-operations...ae0c3e4`: PASS.

Inspected:

- Complete changed-file list and commit metadata.
- Changed SQL migrations.
- Middleware and shared server authorization helpers.
- Every call site of `isManagerAuthenticated()`, `requireStaffRole()`, and `getCurrentStaff()`.
- Deleted legacy manager-auth files and residual runtime references.
- Operational RPC revoke/grant pairs.
- Added diff lines for likely credential material.
- Claude’s implementation report.

No full build was repeated because the task explicitly said not to repeat it without necessity and the implementation report records passing TypeScript, ESLint, and production build checks.

No live PostgreSQL/Supabase instance was used. SQL conclusions are based on static PostgreSQL and Supabase semantics.

## 7. Residual risks

1. The high-severity soft-deleted-profile API bypass must be fixed before merge.
2. RLS and trigger corrections still require live role-matrix validation against Supabase/PostgreSQL.
3. Tests should cover:
   - anonymous users;
   - every staff role;
   - inactive profiles;
   - soft-deleted profiles;
   - deleted role assignments;
   - direct `/api/manager/*` requests;
   - lead and booking insertion/status transitions;
   - operational RPC calls as `anon`;
   - missing Supabase configuration.
4. The security-definer functions rely on the migration function owner having the intended controlled ownership and RLS-bypass behavior. Deployment should verify function owners and effective grants.
5. Two previously documented high dependency advisories through Next.js’s optional `sharp` dependency remain unchanged. Forced audit remediation remains prohibited.
6. Housekeeping and technician operational interfaces remain incomplete.
7. Migration replay remains dependent on the migration ledger.
8. README documentation should be updated after the security correction to remove the deleted PIN route.

## 8. Final verdict

**FAIL**

Commit `ae0c3e4` resolves the original critical defect and all original high blockers, but server-side manager API authorization does not reject soft-deleted profiles. This leaves a realistic account-revocation bypass protecting sensitive manager APIs, including a service-role-backed data endpoint.

No report file was created because the re-audit restrictions explicitly required read-only operation and prohibited modifying files.