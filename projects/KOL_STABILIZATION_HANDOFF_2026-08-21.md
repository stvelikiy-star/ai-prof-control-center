# KÖL — Stabilization Handoff — 2026-08-21

This is the latest current-state addendum for `projects/KOL.md`. The 2026-08-20 handoff remains historical context only. This document does **not** authorize live or production actions.

## Current source truth

KÖL repo: `stvelikiy-star/kol-travel-platform`

Current confirmed `main` at this handoff:

`1cb37e622ae2818debc514f6a372747a3dc2a132`

Framework on current main:

- Next.js 16.3.1
- React 19.2.x
- Node >=22
- TypeScript 5.7.x
- ESLint 9 flat config

CI is established and is no longer an unresolved bootstrap problem.

## Local Supabase transaction proof — PR #39

Head:

`e8830ad388b5db76efd4e9e3c62820cbd39c4c65`

Proof:

- KOL CI `32408679214`: PASS
- KOL Local Supabase Staging Smoke `32408679201`: PASS
- 21/21 staged migration layers applied to a disposable local PostgreSQL/Auth/Storage stack

Tested behavior includes:

- Stay authoritative pricing/idempotency/payload mismatch + real two-session last-room race;
- Tour authoritative pricing/capacity/idempotency/payload mismatch;
- Shop authoritative stock/totals/idempotency + real two-session last-item race;
- payment authoritative amount, event replay, replay conflict, mismatch rejection, settlement and refund-auto-off;
- delivery role guards, canonical state machine, terminal cleanup and idempotency;
- staged RLS/search_path/index/private-Storage invariants.

`011c_payment_service_role_acl_DRAFT_NOT_APPLIED.sql` was added after functional testing exposed the minimum ACL required by the SECURITY INVOKER payment RPC path.

No live Supabase SQL/Auth/Storage mutation occurred.

## Dependency security — PR #40

Head:

`bc39a469a83e4ac8f0ea5345e00d42ff3110c7ea`

Exact fix:

- root dev/tooling `brace-expansion 1.1.15 -> 1.1.18` in tracked lockfile;
- `package.json` unchanged;
- permanent full dependency HIGH audit added to CI;
- no force fix.

Final proof:

- KOL CI `32410302609`: FULL PASS
- `npm ci`: 0 vulnerabilities
- production audit: 0 vulnerabilities
- full dependency audit: 0 vulnerabilities
- schema/staging/deployment/lint/TS/build: PASS
- Local Supabase smoke `32410302546`: PASS

PR #40 remains draft/unmerged.

## Navigation lint cleanup — PR #41

Head:

`a8f22984e67b58bb95223d6df9881d010e379140`

Current base is intentionally PR #40 branch.

Changes:

- temporary `@next/next/no-html-link-for-pages: off` removed;
- 17 lint-reported internal anchors across 13 TSX files converted to `next/link`;
- no business/data/payment/booking/order/delivery logic change.

Combined stacked proof:

- KOL CI `32411427169`: FULL PASS
- production audit: PASS
- full dependency audit: PASS
- schema/staging/deployment: PASS
- lint with rule restored: PASS
- TypeScript: PASS
- Next 16.3.1 production build: PASS

PR #41 remains draft/unmerged.

## Technical Master Context — PR #42

Head:

`26b5f2f8d4afdb67b46371fdd7b6ec44e0a81243`

- docs-only V5
- KOL CI `32411986430`: PASS
- records main-vs-draft separation, local transaction proof, dependency/lint hardening, owner gates and no-go rules
- remains draft/unmerged

## Correct current staged DB sequence

After a real logical backup + accepted authoritative migration baseline, staging/rehearsal only:

`005 -> 005a -> 006 -> 006a -> 006b -> 006c -> 010 -> 007 -> 007a -> 007b -> 008 -> 008a -> 009 -> 009a -> 011 -> 011a -> 011b -> 011c -> 012 -> 012a -> 012b`

All DB apply files remain `DRAFT_NOT_APPLIED`.

## Current live Supabase boundary

Live project remains the recovered `kol-travel-platform-test` / `mphruawzozrpwcjgejhs` baseline.

Last read-only facts still include:

- 54 public tables / 54 RLS enabled;
- 46 live policies before drafts;
- 26 RLS tables with zero policies before drafts;
- missing migration ledger;
- 0 payment rows;
- 0 Storage buckets/objects;
- leaked-password protection disabled;
- no Supabase development branches at last check.

Do not assume the locally proved draft stack is live.

## Safe source merge order — only after explicit approval

1. merge PR #40 into KÖL `main`;
2. retarget PR #41 to the updated `main`;
3. require fresh green CI;
4. merge #41 only if still green;
5. evaluate #39 separately;
6. local DB proof never authorizes live SQL apply;
7. PR #42 documentation can follow the accepted source state.

## Remaining owner gates

Tracked in KÖL issue #16:

1. real logical DB backup/schema baseline + rollback procedure;
2. Supabase Auth leaked-password protection decision/enablement;
3. payment provider and financial/cancellation/payout rules;
4. production secrets/environment;
5. explicit live migration/payment/deployment approval.

A cost-bearing Supabase development branch also requires explicit cost confirmation.

## Production state

- KÖL Vercel project/staging/production/domain: not established at last check;
- no production deployment performed;
- no real payment provider connected;
- no charge/refund performed;
- automatic refunds OFF;
- alcohol OFF.

## Never claim now

Do not claim:

- draft SQL is live;
- a trustworthy live migration ledger exists;
- local disposable proof is a live backup;
- live staging E2E has passed;
- payment provider/business rules are decided;
- Vercel production is deployed;
- production readiness is approved.

## Immediate next boundary

Source/local technical uncertainty has been reduced substantially. The next material gate is not another mock transaction design pass.

It is:

**authoritative backup/migration baseline -> approved staging/rehearsal -> live-target RBAC/E2E/concurrency/rollback acceptance -> explicit production release.**
