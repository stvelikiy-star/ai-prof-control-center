# KÖL CURRENT STATE

Updated: 2026-08-29.

## Project isolation

- This agent package is for `project=kol` only.
- Canonical repository: `stvelikiy-star/kol-travel-platform`.
- Canonical local path from `orchestrator/projects.json`: `/home/agent/projects/kol-travel-platform`.
- Do not read, modify, enqueue, recover or reinterpret tasks for AK BERMET or any other project while operating as the KÖL agent.
- Ignore stale Night Watch branches and historical queue records as current truth unless they are re-verified against current `main` and runtime evidence.

## Verified source baseline

- KÖL audit-start `main`: `960bad09a9a6c07409984b5f1ca903b45c852afd`.
- Exact-main KOL CI run #413 was rerun on the same SHA on 2026-08-29 and completed successfully without source changes; the earlier failure on that run is therefore not evidence of a current code regression.
- `main` is not protected by GitHub branch protection/rulesets; green CI is evidence, not an enforced merge policy.
- Production source is intentionally fail-closed: `PRODUCTION_RUNTIME_IMPLEMENTATION_READY=false`.
- Public/non-production demo may run in explicit mock mode; mock/demo evidence must never be reported as production evidence.

## Current audit findings

- Supabase project visible for KÖL is `kol-travel-platform-test` (`mphruawzozrpwcjgejhs`) and is currently `INACTIVE`; do not claim live database health or restore/mutate it without an explicitly authorized operation.
- Public Vercel Demo Center is a mock presentation surface and explicitly identifies freeze `874841bbcef8026b18ffe3a0cbed1884ea1320d3`; it is not proof that current GitHub `main` is deployed to production.
- Multiple historical/probe/freeze KÖL Vercel projects exist. Do not delete, promote or call one canonical without source/deployment evidence.
- Current README contains mojibake/stale presentation text and requires factual cleanup.
- Full audit found a production-environment precedence fail-open: a manual `KOL_DEPLOYMENT_ENV` could override `VERCEL_ENV=production`. Repair is in KÖL PR #57 and must not be reported fixed until exact-head CI passes and the PR is merged.

## Night Watch execution objective

Work continuously on KÖL owner-free completion only:

1. Audit current KÖL `main` before editing.
2. Fix one proven root cause at a time; prefer existing work over duplicate tasks.
3. Preserve Auth/RLS/ownership/payment safety boundaries and production fail-closed behavior.
4. Run repository-native checks after every repair and the full KÖL CI before merge.
5. Keep production/Supabase restore, deployment, secrets, payment activation and destructive data changes behind explicit operation/owner gates.
6. Report `VERIFIED/PASS`, `IMPLEMENTED`, `BLOCKED`, and `UNKNOWN` separately; never turn preview/mock evidence into production PASS.
