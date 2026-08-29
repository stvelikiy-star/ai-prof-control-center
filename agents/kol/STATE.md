# KÖL CURRENT STATE

Updated: 2026-08-29.

## Project isolation

- This agent package is for `project_id=kol-travel-platform` only.
- Canonical repository: `stvelikiy-star/kol-travel-platform`.
- Canonical local path: `/home/agent/Загрузки/kol-travel-platform`.
- Never read, modify, enqueue, recover or reinterpret tasks for AK BERMET or any other project while operating as the KÖL agent.
- Ignore stale queue/history/branches unless re-verified against current `main` and runtime evidence.

## Verified source baseline

- KÖL canonical `main`: `284cf333485e94ad7ec51a9ce49446426a6be4f3` unless GitHub main has legitimately advanced.
- PR #57 production fail-closed repair passed KOL CI #422, Public Flows #214 and Visual QA #251 before merge.
- PR #58 current-state README repair passed KOL CI #424, Public Flows #215 and Visual QA #252 before merge.
- `VERCEL_ENV=production` is authoritative and must not be downgraded by `KOL_DEPLOYMENT_ENV`.
- `PRODUCTION_RUNTIME_IMPLEMENTATION_READY=false` remains a hard source gate until explicit production acceptance.
- Mock/preview evidence is never production evidence.

## External/runtime facts

- KÖL Supabase `mphruawzozrpwcjgejhs` remains INACTIVE. Ordinary Night Watch tasks must not restore, unpause, migrate or mutate it.
- Historical Vercel READY deployments are not proof that current `main` is the business-production runtime.
- Issue #16 remains the canonical P0 owner/production gate: active Supabase/staging/backup evidence, real Auth/security acceptance, payment and fee/refund/payout decisions, production secrets/config, staging E2E, production-readiness approval and final deploy approval.
- `main` is not protected by GitHub branch protection; CI evidence must therefore be checked explicitly on the exact head.

## Maximum owner-free capability

The KÖL execution profile is intentionally widened for the launch push to cover source plus repository-native QA/release contracts:

- `README.md`, `docs/**`, `src/**`, `app/**`, `components/**`, `lib/**`, `public/**`, `tests/**`, `supabase/**`;
- `scripts/**`, `.github/workflows/**`, `.env.example`;
- `package.json`, TypeScript/Next/Tailwind/PostCSS/middleware config and `vercel.json`.

This wider source scope does **not** grant commit, push, merge, deployment, secrets, credentials, live DB mutation, Supabase restore, production config mutation or payment activation authority.

## Mandatory verification

For every meaningful code repair run relevant touched-module checks. Final source gate must include:

1. `npm run lint`
2. `npx tsc --noEmit --incremental false`
3. `npm run check:release-source`
4. `npm run build`

`check:release-source` includes dependency audit, Supabase source/staging checks, live-baseline read-only verification, deployment environment checks/selftests, lint, typecheck and build. Do not bypass a failing gate to manufacture progress.

## Night Watch objective until launch

1. Audit actual current `main` before every repair chain.
2. Continue KÖL only; never create activity-only duplicate tasks.
3. Fix proven owner-free root causes one at a time, including QA/release scripts when the defect is real.
4. Preserve Auth/RBAC/RLS/ownership, finance fail-closed, booking/order integrity and production fail-closed invariants.
5. Do not invent payments, commissions, refunds, payouts, bookings, orders, partner data or production status.
6. When a real owner/production gate is reached, stop only that blocked path and continue other safe owner-free KÖL work.
7. Report `FACTS`, `FIXED`, `VERIFIED`, `UNVERIFIED`, `CAPABILITY_BLOCKER`, `OWNER_ACTION_REQUIRED`, `RELEASE-READINESS` separately.
