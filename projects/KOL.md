# KÖL / KOL TRAVEL PLATFORM

## Identity

- Product: KÖL
- Technical repository: `stvelikiy-star/kol-travel-platform`
- Region/product scope: Issyk-Kul marketplace / super-app
- Core domains: Tours, Stay, Food, Shop
- Roles/surfaces: Client, Partner, Courier, Admin
- Separate project: never mix with AK BERMET, PALADIN or other AI PROF projects

## Core architecture authority

- ONE ECOSYSTEM / ONE ACCOUNT / ONE TRANSACTIONAL CORE.
- Supabase/PostgreSQL is authoritative for price, inventory, availability, booking/order/payment state and authorization-sensitive writes when live mode is enabled.
- Booking/order/payment/delivery truth must use deterministic server/database logic and narrow trusted RPCs.
- AI may interpret intent and call tools, but must never invent price, availability, stock, discount, payment state, booking confirmation, refund state or fees.
- Production must fail closed instead of silently serving mock/demo transactional truth.

## Git source of truth

- Recovery baseline: `7e713b19f6c73c329c09df1163afba17c5443096`
- Current confirmed `main` at 2026-08-21 handoff: `1cb37e622ae2818debc514f6a372747a3dc2a132`
- Historical Git lineage before the recovery baseline was not recovered and must not be fabricated.

Current framework baseline on `main`:

- Next.js `16.3.1`
- React / React DOM `19.2.x`
- Node engine `>=22`
- TypeScript `5.7.x`
- ESLint 9 flat config
- Tailwind CSS `3.4.x`
- `@supabase/ssr` `0.12.4`
- `@supabase/supabase-js` `2.111.0`

## Live Supabase — last read-only verified state

- project: `kol-travel-platform-test`
- ref: `mphruawzozrpwcjgejhs`
- region: `ap-northeast-2`
- PostgreSQL: `17.6.1.127`
- status: `ACTIVE_HEALTHY`
- public tables: 54
- RLS enabled: 54/54
- live policies before staged drafts: 46
- RLS-enabled tables with zero policy before staged drafts: 26
- public helper/trigger functions: 6
- public indexes: 99
- single-column public foreign keys: 80
- missing leading FK indexes before staged drafts: 49
- Auth users: 4 recovery/demo identities
- payments rows: 0
- Storage buckets: 0
- Storage objects: 0
- tracked Supabase migration ledger: absent
- Supabase development branches at last check: 0
- Stage 21 additive catalog migration: not applied

Never store keys/passwords/tokens in this project file.

No live SQL/Auth/Storage mutation was performed during the current source/local-staging proof.

## Live security facts motivating the draft stack

Last read-only live audit identified:

- `user_roles -> is_admin() -> has_role() -> user_roles` recursion path;
- `partner_staff -> is_partner_for() -> partner_staff` recursion risk;
- six public helper/trigger functions without fixed search path;
- 26 RLS-enabled public tables with zero policies;
- leaked-password protection disabled;
- broad grants requiring explicit least-privilege treatment;
- 49 missing leading FK indexes for checked single-column public FKs.

## Current staged DB sequence — NOT LIVE APPLIED

After a real backup + accepted migration baseline, staging/rehearsal only:

1. `005` security hardening
2. `005a` partner policy scope
3. `006` RLS policy completion
4. `006a` audit write lockdown
5. `006b` RLS init-plan/scope hardening
6. `006c` transaction entrypoint lockdown
7. `010` FK index baseline
8. `007` Stay/Tour booking transaction core
9. `007a` booking direct-write lockdown
10. `007b` booking idempotency serialization
11. `008` Food/Shop order transaction core
12. `008a` order idempotency payload hardening
13. `009` private catalog media Storage
14. `009a` media FK-index correction
15. `011` payment integrity
16. `011a` provider-event replay conflict guard
17. `011b` payment projection hardening
18. `011c` minimum `service_role` ACL required by payment SECURITY INVOKER RPCs
19. `012` delivery lifecycle
20. `012a` delivery assignment consistency
21. `012b` delivery role/state consistency hardening

All DB apply files remain `DRAFT_NOT_APPLIED`.

## Current verified source/local-staging proof

### PR #39 — transaction behavior + concurrency

- draft/open
- head: `e8830ad388b5db76efd4e9e3c62820cbd39c4c65`
- KOL CI `32408679214`: PASS
- Local Supabase Staging Smoke `32408679201`: PASS
- 21/21 staged layers applied to a disposable local PostgreSQL/Auth/Storage stack

Proved under the tested contracts:

- Stay replay, DB pricing, payload mismatch rejection and real two-session last-room race;
- Tour replay/capacity/DB pricing/payload mismatch rejection;
- Shop normalized-cart replay, stock decrement-once, DB totals and real two-session last-item race;
- payment authoritative amount, settlement, exact replay, replay conflict, amount mismatch fail-closed and refund auto-apply OFF;
- delivery role guards, canonical state machine, terminal cleanup, payment-truth isolation and replay idempotency;
- staged 54/54 RLS retention, zero staged policy-less RLS tables, fixed helper search paths and checked FK index completion.

Important: disposable local proof is not evidence of live apply.

### Payment ACL correction from functional testing

`011c_payment_service_role_acl_DRAFT_NOT_APPLIED.sql` adds only the trusted-server table privileges required by the SECURITY INVOKER payment path:

- orders/bookings: SELECT + UPDATE
- payments: SELECT + INSERT + UPDATE
- order_payments: SELECT + INSERT
- audit_logs: INSERT

Browser/session mutation remains closed.

### PR #40 — dependency hardening

- draft/open against `main`
- head: `bc39a469a83e4ac8f0ea5345e00d42ff3110c7ea`
- exact fix: root dev/tooling `brace-expansion 1.1.15 -> 1.1.18`
- `package.json` unchanged
- permanent full dependency HIGH audit gate added
- KOL CI `32410302609`: FULL PASS
  - `npm ci`: 0 vulnerabilities
  - production audit: 0
  - full dependency audit: 0
  - schema/staging/deployment/lint/TS/build: PASS
- local Supabase smoke `32410302546`: PASS
- no `npm audit fix --force`

### PR #41 — internal navigation lint cleanup

- draft/open
- intentionally stacked on PR #40
- head: `a8f22984e67b58bb95223d6df9881d010e379140`
- removes temporary `@next/next/no-html-link-for-pages: off`
- converts 17 lint-reported internal anchors across 13 TSX files to `next/link`
- combined KOL CI `32411427169`: FULL PASS including full dependency audit, lint, TypeScript and Next 16.3.1 build
- no business/data/payment/booking/order/delivery logic change

### PR #42 — Technical Master Context V5

- draft/open, docs-only
- head: `26b5f2f8d4afdb67b46371fdd7b6ec44e0a81243`
- KOL CI `32411986430`: PASS
- records current main-vs-draft separation, owner gates and no-go rules

## Current GitHub CI expectation

Normal source checks include:

- `npm ci`
- production dependency audit
- schema manifest
- staging execution package
- deployment preflight
- deployment fail-closed self-tests
- lint
- TypeScript
- Next production build in intentional mock mode

PR #40 additionally makes full dependency HIGH audit permanent after merge.

Never claim PASS for a check that was not actually executed.

## Vercel / deployment state

Last connected-team audit found no established KÖL Vercel project/deployment/env/domain.

Therefore, unless newer explicit evidence exists:

- KÖL preview/staging: NOT ESTABLISHED
- KÖL production: NOT ESTABLISHED
- production URL/domain: NOT ESTABLISHED
- production release: NOT AUTHORIZED

Do not create production as the first environment.

## Storage state

Live at last check:

- buckets: 0
- objects: 0

The staged `catalog-media` design is private and locally verified; that does not mean a live bucket exists.

DB backup and Storage backup are separate and must not be conflated.

## Payment state

Provider-neutral payment transaction/replay integrity is locally proven, but activation remains blocked by owner/business decisions.

Never invent:

- payment provider
- commission/service fee
- delivery fee policy
- cancellation/refund/no-show rules
- partner payout rules

Automatic refund application remains OFF. No real charge/refund has occurred.

## Safe source merge order — explicit approval required

Do not infer merge permission from autonomous source-work permission.

When the owner explicitly approves source merge:

1. merge PR #40 into `main`;
2. retarget PR #41 back to updated `main`;
3. require fresh green CI on the new `main` base;
4. merge PR #41 only if that final run remains green;
5. review PR #39 separately; local DB proof is never authorization to apply SQL live;
6. PR #42 is documentation-only and can follow the accepted source state.

## Consolidated owner gates

Operational checklist: KÖL issue #16.

### Gate 1 — authoritative DB backup / migration baseline

Before any live SQL:

- real logical DB backup/schema dump;
- accepted authoritative baseline;
- rollback procedure;
- no fabricated migration history.

### Gate 2 — Auth leaked-password protection

Security Advisor last reported it disabled. Resolve before production Auth acceptance.

### Gate 3 — payment/business rules

Owner approval required for provider, fees, cancellation/refund/no-show and payout policy.

### Gate 4 — production secrets/environment

Secrets must be configured in hosting/Supabase secret systems, never Git/tasks/docs.

### Gate 5 — production release

Explicit owner approval required before:

- live DB migration;
- payment activation;
- Vercel production deployment.

## Production acceptance minimum

Require at least:

1. logical DB backup/schema baseline + rollback procedure;
2. controlled migration rehearsal on approved staging or equivalent isolated target;
3. E2E/RBAC/transaction/concurrency verification against that target;
4. leaked-password protection decision/enablement;
5. approved provider/fees/cancellation/refund/no-show/payout rules;
6. production secrets/live Supabase config;
7. observability/rollback acceptance;
8. explicit production approval.

## Safety / forbidden automatic actions

Without an explicit approved gate:

- no destructive SQL
- no production DB migration
- no live Auth/Storage mutation for testing convenience
- no Stage 21 apply
- no payment provider activation
- no alcohol module activation
- no production deploy
- no cost-bearing Supabase branch without cost confirmation
- no secrets in Git/tasks/docs
- no fabricated migration history or historical Git commits
- no assumption that demo/mock data is production data
- no AI authority over availability, price, stock, payment or booking truth
- no direct client-supplied totals/availability/stock truth
- no blind DB restore after real provider settlements
- no `npm audit fix --force`
- no silent merge of draft PRs

## Current priority boundary

The transaction design is no longer merely theoretical: disposable local proof is green under the tested contracts.

The next material boundary is:

**controlled transition from recovered live schema to an authoritative backup/migration baseline and an approved staging/release process.**

Continue source-only audits, CI, documentation, runbooks and fail-closed verification autonomously until an owner gate is actually required.
