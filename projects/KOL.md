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
- PR #22 — provider-neutral payment integrity stack on #13
  - service-role-only payment attempt/event RPCs
  - amount and payer derived from authoritative order/booking state
  - unique provider reference + idempotent provider event ledger
  - raw provider payload not stored; SHA-256 hash + sanitized metadata only
  - paid amount mismatch fails closed
  - duplicate settlements preserved/audited for review
  - automatic refund application remains OFF
  - direct browser/session payment mutation closed
- PR #23 — atomic delivery lifecycle stack on #13
  - closes broad direct delivery mutation from authenticated sessions
  - dispatcher-only safe courier assignment
  - exact courier-owned physical state machine
  - assignment/status/history/audit atomic
  - pickup/delivered may update eligible operational order status
  - payment_status is never mutated by delivery RPCs
  - delivery pricing/row creation remains separate until fee policy exists
- PR #24 — observability/rollback stack on #20
  - `x-request-id` correlation through middleware/session refresh
  - health endpoint explicitly distinguishes configuration readiness from DB connectivity
  - deterministic `check:release-source`
  - rollback/recovery runbook
  - source and staged migration dependency order
  - financial recovery requires provider reconciliation after payment activation
  - Storage object bytes require separate backup from DB metadata
- PR #25 — minimal CI bootstrap directly against `main`
  - one workflow file only
  - every pull request + push to main + manual dispatch
  - npm ci → schema manifest → lint → TypeScript → build
  - deliberately isolated so CI can be accepted independently from #13

PR #17 contains the current technical Master Context V4. Consolidated owner-only gates are tracked in KÖL issue #16.

## Required checks

Baseline source checks:

- `npm ci`
- `npm run check:supabase-schema-files`
- `npm run check:deployment-env`
- `npm run lint`
- `npx tsc --noEmit --incremental false`
- `npm run build`
- on #24+: `npm run check:release-source`

Database/security work additionally requires:

- schema drift/fingerprint check
- Supabase Security Advisor
- Supabase Performance Advisor review
- role-by-role RLS test
- cross-partner isolation test
- concurrency/idempotency tests for inventory/booking/order writes
- payment replay/amount/duplicate-settlement tests
- delivery ownership/state-machine/concurrency tests
- Storage cross-partner/public-read tests
- rollback verification

Do not claim PASS when a check was not actually executed.

Verified deployment preflight cases on 2026-08-20:

- development + mock + alcohol off: PASS
- production + mock: FAIL as required
- production + Supabase public config: PASS
- public secret-like env name: FAIL as required

Fresh full repo lint/TypeScript/build on the current draft branches is still NOT proven. The execution container cannot resolve `github.com`, and GitHub Actions currently has zero workflow runs for the transactional PR heads.

CI root cause verified 2026-08-20:

- `.github/workflows/ci.yml` exists only on the unmerged stabilization/security branch, not `main`;
- the old draft workflow targeted only pull requests whose base is `main`, so stacked PRs did not match it;
- PR #25 isolates CI bootstrap directly against `main`; it is NOT merged automatically.

## Vercel preflight

Live connected Vercel account was re-audited 2026-08-20:

- connected team: `ai prof kg` / `ai-prof-kg`;
- existing projects in that team are Whieda and PALADIN projects;
- no KÖL / `kol-travel-platform` Vercel project exists;
- therefore KÖL has no Vercel deployment/env/domain to inspect yet;
- no project or deployment was created during this audit.

A KÖL Vercel preview/staging should be created only after source CI and the intended staging data-source contract are ready. Production must not be created as the first environment.

## Architecture authority

- Supabase/PostgreSQL is the authoritative transactional data source when live mode is enabled.
- Availability, inventory, totals, booking/order state and payment truth must be server/database authoritative.
- Booking/order creation should use narrow transactional RPCs, not direct Data API INSERTs.
- Audit rows for business mutations should be emitted from the trusted transaction/server path, not accepted from arbitrary clients.
- Payment callbacks may affect financial truth only after provider-specific signature verification and through an idempotent trusted transaction.
- Delivery physical progress is a narrow state machine; couriers never own price/payment/order-item truth.
- AI may interpret intent and call deterministic tools, but may not invent price/availability/discount/payment/booking confirmation.
- Production must fail closed rather than silently falling back to mock/demo data.
- Catalog media uses a private Storage design; public catalog media is exposed through policy-checked signed URLs.

## Current staged migration candidate order

After authoritative backup/baseline and only on staging:

1. 005 security hardening
2. 005a partner scope
3. 006 policy completion
4. 006a audit write lockdown
5. 006b RLS init-plan/scope hardening
6. 006c transaction entrypoint lockdown
7. 010 FK index baseline
8. 007 Stay/Tour booking core
9. 008 Food/Shop order core
10. 009 catalog media Storage
11. 011 payment integrity
12. 012 delivery lifecycle

Every staged apply must be followed by the corresponding VERIFY file plus relevant role/concurrency tests before the next transactional layer is accepted.

## Current priority sequence

1. Accept CI bootstrap (#25) so future source checks can actually run on GitHub.
2. Obtain real logical backup/schema dump and accept authoritative migration baseline.
3. Stage PR #13 security package and prove RBAC/tenant isolation/advisor results.
4. Stage #21 FK index package after correctness baseline.
5. Stage #15 Stay/Tour transaction core with initialized inventory and concurrency tests.
6. Stage #18 Food/Shop transaction core with stock/pricing/idempotency tests.
7. Stage #19 Storage/media with cross-partner and signed-read tests.
8. Stage #22 payment integrity only after a provider-specific signature adapter is chosen/testable; no provider activation yet.
9. Stage #23 delivery lifecycle independently of delivery pricing; fee creation remains blocked until business rule exists.
10. Use #20/#24 for preview/staging environment, health, correlation and rollback drill.
11. Reconcile accepted drafts into one tracked forward migration sequence; do not fabricate historical migrations.
12. Production pilot only after E2E/concurrency/backup-rollback/observability and owner acceptance.

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
- no blind DB restore after real provider settlements

## Current production state

- Vercel KÖL project: confirmed absent from the connected AI PROF team as of 2026-08-20.
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
