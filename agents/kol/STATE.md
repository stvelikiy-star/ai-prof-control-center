# KÖL CURRENT STATE

Updated: 2026-08-29.

## Project isolation

- This agent package is for `project_id=kol-travel-platform` only.
- Canonical repository: `stvelikiy-star/kol-travel-platform`.
- Canonical local path in the current Control Center registry: `/home/agent/Загрузки/kol-travel-platform`.
- Do not read, modify, enqueue, recover or reinterpret tasks for AK BERMET or any other project while operating as the KÖL agent.
- Ignore stale Night Watch branches and historical queue records as current truth unless they are re-verified against current `main` and runtime evidence.

## Verified source baseline

- KÖL canonical `main` after the 2026-08-29 full audit repairs: `284cf333485e94ad7ec51a9ce49446426a6be4f3`.
- PR #57 `fix(security): keep Vercel production fail-closed` was squash-merged only after exact-head SUCCESS on KOL CI #422, KOL Public Flows #214 and KOL Visual QA #251.
- PR #58 replaced the corrupted/obsolete README and was squash-merged only after current-base SUCCESS on KOL CI #424, KOL Public Flows #215 and KOL Visual QA #252.
- The production-environment precedence bug is fixed in source: real `VERCEL_ENV=production` cannot be downgraded by manual `KOL_DEPLOYMENT_ENV`.
- Release/public/finance/operational QA drift discovered during the audit was reconciled without removing forbidden/demo safety assertions.
- Production source remains intentionally fail-closed: `PRODUCTION_RUNTIME_IMPLEMENTATION_READY=false`.
- `main` is not protected by GitHub branch protection/rulesets; green CI is evidence, not an enforced repository policy.
- Public/non-production demo may run in explicit mock mode; mock/demo evidence must never be reported as production evidence.

## Current external/runtime facts

- Supabase project visible for KÖL is `kol-travel-platform-test` (`mphruawzozrpwcjgejhs`) and remains `INACTIVE`; do not claim live database health or restore/mutate it without an explicitly authorized operation.
- `kol-travel-platform-app` exists in Vercel and historical READY deployments exist, but no inspected deployment is accepted as proof that current GitHub `main` is the business-production runtime.
- Multiple historical/probe/freeze KÖL Vercel projects exist. Do not delete, promote or call one canonical without source/deployment evidence.
- GitHub issue #16 remains the canonical P0 owner/production gate: Supabase restore/baseline/backup/staging, Auth/security acceptance, payment-provider and business-rule decisions, production secrets/config, current-main staging E2E and explicit production approval remain outside ordinary owner-free tasks.

## Documentation status

- Root README now reflects the current Next.js 16.3.1 architecture, production fail-closed contract, P0 gates and verification workflow.
- Dated `docs/` reports are historical evidence unless re-verified against current source/runtime.

## Registry capability boundary

- The registered KÖL execution profile currently allows `README.md`, `docs/**`, `src/**`, `app/**`, `components/**`, `lib/**`, `public/**`, `tests/**`, `supabase/**` and selected project config files.
- It does not currently authorize ordinary KÖL tasks to edit `scripts/**`, `.github/workflows/**` or `.env.example`.
- Do not widen scope inside a task or pretend those paths are writable. If a verified root cause requires them, return the exact capability blocker for coordinator review instead of bypassing the registry.

## Night Watch execution objective

Work continuously on KÖL owner-free completion only:

1. Audit current KÖL `main` before editing.
2. Fix one proven root cause at a time; prefer existing work over duplicate tasks.
3. Preserve Auth/RLS/ownership/payment safety boundaries and production fail-closed behavior.
4. Stay inside the registered Scope-Files and run repository-native checks after every repair.
5. For code tasks, at minimum run the registered `npx tsc --noEmit` and `npm run build`; also run any relevant repository-native checks available within task scope.
6. Keep production/Supabase restore, deployment, secrets, payment activation and destructive data changes behind explicit operation/owner gates.
7. Report `VERIFIED/PASS`, `IMPLEMENTED`, `BLOCKED`, and `UNKNOWN` separately; never turn preview/mock evidence into production PASS.
