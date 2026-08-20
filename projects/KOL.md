# KÖL / KOL TRAVEL PLATFORM

## Identity

- Product: KÖL
- Technical name: `kol-travel-platform`
- Region/product scope: Issyk-Kul marketplace / super-app
- Core domains: Tours, Stay, Food, Shop
- Separate project: do not mix with AK BERMET, PALADIN or other AI PROF projects

## Repository / source of truth

- GitHub: `stvelikiy-star/kol-travel-platform`
- Base branch: `main`
- Recovered workstation source path: `/home/agent/Загрузки/kol-travel-platform`
- Recovery baseline established: 2026-08-13
- Current confirmed main before 2026-08-20 draft work: `a413fb6fb5b5361b3aeab9de6801050704370896`

GitHub `main` is the code source of truth. Historical Git history before the recovery baseline was not recovered and must not be fabricated.

## Backend

- Supabase project: `kol-travel-platform-test`
- Project ref: `mphruawzozrpwcjgejhs`
- Region: `ap-northeast-2`
- PostgreSQL: `17.6.1.127`
- Status verified 2026-08-20: `ACTIVE_HEALTHY`

Never store keys/passwords/tokens in this project file.

## Current recovery status

Confirmed source/application state:

- Next.js 14.2.23
- React 18.3.1
- TypeScript 5.7.3
- Tailwind CSS 3.4.17
- `@supabase/ssr` 0.12.4
- `@supabase/supabase-js` 2.111.0
- historical production build recovery: PASS, 130/130 pages generated
- Client / Partner / Courier / Admin application surfaces exist
- Auth/session/route-guard source foundation exists
- Supabase read adapters exist for multiple cabinet/catalog surfaces

Current live database recovery state:

- public tables: 54
- RLS enabled: 54/54
- live RLS policies before staged drafts: 46
- tables with RLS but zero policy before staged drafts: 26
- public helper/trigger functions: 6
- public indexes: 99
- single-column foreign keys: 80
- missing leading FK indexes: 49
- Auth users: 4 demo/recovery identities
- payments: 0
- Storage buckets: 0
- Storage objects: 0
- tracked Supabase migration ledger: absent
- Stage 21 additive catalog migration: not applied

This database is a recovery/demo state, not production customer data.

## Current active technical work

Repository draft PRs prepared on 2026-08-20:

- PR #13 — P0 RLS/security baseline; DB drafts are NOT applied
  - breaks user_roles/partner_staff recursion
  - hardens function search paths
  - completes conservative policies for all previously policy-less tables
  - closes direct authenticated audit-log mutation
  - closes unsafe direct client INSERT entrypoints for orders/bookings
  - rewrites remaining identity RLS predicates to init-plan-friendly authenticated scope
- PR #14 — fail closed on unsafe production mock/data-source configuration
- PR #15 — atomic Stay/Tour booking transaction draft; DB draft is NOT applied
  - includes direct booking INSERT lockdown
- PR #18 — atomic Food/Shop order transaction core stacked on #15
  - DB-side pricing
  - shop stock locks/decrement
  - idempotency
  - atomic partner ready-for-pickup status/history/audit
  - pickup-only until an authoritative delivery-fee model exists
- PR #19 — secure private catalog-media Storage stack on #13
  - private bucket contract
  - partner-scoped upload/delete
  - anon active-catalog signed URL reads
  - no service-role browser path
- PR #20 — staging-readiness stack on #14
  - `.env.example`
  - deployment preflight
  - shared fail-closed safety snapshot
  - `/api/health`
  - alcohol=true hard block
- PR #21 — additive FK index baseline stacked on #13
  - 49 missing single-column FK indexes
  - no index deletion

PR #17 contains the current technical Master Context V4. Consolidated owner-only gates are tracked in KÖL issue #16.

## Required checks

Baseline source checks:

- `npm ci`
- `npm run check:supabase-schema-files`
- `npm run lint`
- `npx tsc --noEmit --incremental false`
- `npm run build`
- `npm run check:deployment-env` on staging/deployment branches

Database/security work additionally requires:

- schema drift/fingerprint check
- Supabase Security Advisor
- Supabase Performance Advisor review
- role-by-role RLS test
- cross-partner isolation test
- concurrency/idempotency tests for inventory/booking/order writes
- Storage cross-partner/public-read tests
- rollback verification

Do not claim PASS when a check was not actually executed.

Verified deployment preflight cases on 2026-08-20:

- development + mock + alcohol off: PASS
- production + mock: FAIL as required
- production + Supabase public config: PASS
- public secret-like env name: FAIL as required

Fresh full repo lint/TypeScript/build on the current draft branches is still NOT proven because GitHub Actions has not registered a run and the connected GitHub interface does not expose a repository checkout archive for local execution.

## Safety / forbidden automatic actions

Without an explicit approved release gate:

- no destructive SQL
- no production DB migration
- no schema/production data deletion
- no Stage 21 apply
- no payment provider activation
- no alcohol module activation
- no production deploy
- no secrets in Git/tasks/docs
- no fabricated migration history
- no fabricated historical Git commits
- no assumption that demo/mock data is production data
- no AI authority over availability, price, payment status or transactional truth
- no direct client-supplied totals/availability/stock truth

## Architecture authority

- Supabase/PostgreSQL is the authoritative transactional data source when live mode is enabled.
- Availability, inventory, totals, booking/order state and payment truth must be server/database authoritative.
- Booking/order creation should use narrow transactional RPCs, not direct Data API INSERTs.
- Audit rows for business mutations should be emitted from the trusted transaction/server path, not accepted from arbitrary clients.
- AI may interpret intent and call deterministic tools, but may not invent price/availability/discount/payment/booking confirmation.
- Production must fail closed rather than silently falling back to mock/demo data.
- Catalog media uses a private Storage design; public catalog media is exposed through policy-checked signed URLs.

## Current priority sequence

1. Obtain real logical backup/schema dump and accept authoritative migration baseline.
2. Stage PR #13 security package and prove RBAC/tenant isolation/advisor results.
3. Stage #21 FK index package after correctness baseline.
4. Stage #15 Stay/Tour transaction core with initialized inventory and concurrency tests.
5. Stage #18 Food/Shop transaction core with stock/pricing/idempotency tests.
6. Stage #19 Storage/media with cross-partner and signed-read tests.
7. Reconcile drafts into one tracked forward migration sequence; do not fabricate historical migrations.
8. Use #20 for staging environment/health/preflight; create real Vercel staging only after the source/database gates are ready.
9. Payment abstraction/provider only after owner decision.
10. Delivery fee, maps/search, notifications and external integrations.
11. Production pilot only after E2E/concurrency/backup-rollback/observability and owner acceptance.

## Current production state

- Vercel KÖL project: not deployed in the currently connected AI PROF team as of 2026-08-20.
- Production URL: not established.
- Production release status: NOT READY.

## Owner-only decisions

Batch these together to minimize owner interruption:

- payment provider;
- commissions/service fee/delivery fee business policy;
- cancellation/refund/no-show rules;
- production secrets;
- paid Supabase/Vercel staging resources if required;
- destructive migration approval;
- final production approval.
