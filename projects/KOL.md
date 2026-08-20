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
- Current confirmed main at registration: `a413fb6fb5b5361b3aeab9de6801050704370896`

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
- RLS policies: 46
- tables with RLS but zero policy: 26
- public helper/trigger functions: 6
- public indexes: 99
- Auth users: 4 demo/recovery identities
- payments: 0
- Storage buckets: 0
- Storage objects: 0
- tracked Supabase migration ledger: absent
- Stage 21 additive catalog migration: not applied

This database is a recovery/demo state, not production customer data.

## Current active technical work

Repository draft PRs prepared on 2026-08-20:

- PR #13 — live-audited RLS/security baseline; DB drafts are NOT applied
- PR #14 — fail closed on unsafe production mock/data-source configuration
- PR #15 — atomic Stay/Tour booking transaction draft; DB draft is NOT applied

Consolidated owner-only gates are tracked in KÖL issue #16.

## Required checks

Baseline source checks:

- `npm ci`
- `npm run check:supabase-schema-files`
- `npm run lint`
- `npx tsc --noEmit --incremental false`
- `npm run build`

Database/security work additionally requires:

- schema drift/fingerprint check
- Supabase Security Advisor
- Supabase Performance Advisor review
- role-by-role RLS test
- cross-partner isolation test
- concurrency/idempotency test for inventory/booking writes
- rollback verification

Do not claim PASS when a check was not actually executed.

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

## Architecture authority

- Supabase/PostgreSQL is the authoritative transactional data source when live mode is enabled.
- Availability, inventory, totals, booking/order state and payment truth must be server/database authoritative.
- AI may interpret intent and call deterministic tools, but may not invent price/availability/discount/payment/booking confirmation.
- Production must fail closed rather than silently falling back to mock/demo data.

## Current priority sequence

1. Preserve backup and authoritative live-schema baseline.
2. Repair RLS recursion/search-path/grant issues and complete fail-closed policy coverage in staging.
3. Run real Auth/RBAC/tenant-isolation tests.
4. Complete atomic Stay/Tour inventory booking flow.
5. Complete Food/Shop order/inventory transaction core.
6. Establish tracked migration baseline before applying later additive migrations.
7. Storage/media policies.
8. Payment abstraction/provider only after owner decision.
9. Maps/search/notifications/external integrations.
10. Vercel staging + smoke/E2E/concurrency/rollback.
11. Production pilot only after owner acceptance.

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
- destructive migration approval;
- final production approval.
