# KÖL — Stabilization Handoff — 2026-08-20

This is a current-state addendum for `projects/KOL.md`. It does not authorize production actions.

## New source packages completed

- PR #22 — provider-neutral payment integrity.
  - 011 payment integrity RPC/ledger draft.
  - 011a conflicting provider-event replay guard using transaction advisory locking.
  - exact replay is idempotent; same event id with different reference/type/status/amount/hash fails closed.
  - provider-specific webhook/signature verification remains intentionally unimplemented until provider selection.
- PR #23 — atomic delivery lifecycle.
  - 012 delivery lifecycle/state-machine draft.
  - 012a recovered assignment consistency draft.
  - verified recovery inconsistency: active demo delivery has assigned_courier_id, but zero courier_assignments rows and courier profile remains online.
  - 012a fails on contradictory assignments, backfills only missing normalized active assignments, and marks active couriers busy.
- PR #24 — observability/rollback.
  - request-id correlation.
  - safe health endpoint.
  - source release-check script.
  - rollback/recovery runbook.
  - current dependency/apply order.
- PR #25 — isolated one-file GitHub CI bootstrap against main.
  - every PR / push main / manual dispatch.
  - npm ci, schema manifest, lint, TypeScript, build.
  - NOT merged automatically.

## Correct current staged DB candidate order

After a real backup + accepted migration baseline, staging only:

1. 005 security hardening
2. 005a partner policy scope
3. 006 RLS completion
4. 006a audit-log write lockdown
5. 006b RLS init-plan/scope hardening
6. 006c transaction entrypoint lockdown
7. 010 FK index baseline
8. 007 Stay/Tour booking transaction core
9. 008 Food/Shop order transaction core
10. 009 catalog media Storage
11. 011 payment integrity
12. 011a payment event replay conflict guard
13. 012 delivery lifecycle
14. 012a recovered delivery assignment consistency

No item above has been applied to the live recovery Supabase project during this work.

## Infrastructure preflight facts

### GitHub CI

- Current `.github/workflows/ci.yml` is not in `main`.
- The previous draft workflow filtered pull requests to base `main`; stacked PRs target intermediate branches.
- GitHub returned zero workflow runs for current payment/delivery PR heads.
- Execution container cannot resolve github.com, so a local clone/full build could not be performed.
- PR #25 isolates the CI bootstrap needed before reliable automated source checks.

### Vercel

Connected team re-audited: `ai prof kg` (`ai-prof-kg`).

Projects currently visible there are Whieda/PALADIN projects. There is no KÖL / `kol-travel-platform` Vercel project, deployment, environment, or domain. No Vercel project/deployment was created during this audit.

### Supabase staging

`list_branches` for project `mphruawzozrpwcjgejhs` returned zero development branches. There is no hidden Supabase staging branch today.

Creating a Supabase development branch is a cost-bearing operation and requires explicit cost confirmation. Do not create it without the owner gate.

## Immediate blocking gates

Technical work that can be done source-only is substantially prepared. Real verification now requires owner-controlled gates:

1. explicit approval to merge the isolated CI bootstrap PR #25 (or an equivalent default-branch CI setup);
2. a fresh logical backup / accepted migration baseline;
3. explicit cost confirmation if using a Supabase development branch for staging;
4. payment provider + delivery/service fee + cancellation/refund/no-show business decisions before those business paths can be activated;
5. production remains blocked.

## Never claim yet

- no fresh full current-branch build PASS;
- no staged SQL apply PASS;
- no current role-by-role RLS PASS against the drafted policies;
- no current transaction concurrency PASS for 007/008/011/012;
- no Vercel preview PASS;
- no production readiness.
