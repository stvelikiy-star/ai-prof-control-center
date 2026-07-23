# TASK AB-PHASE-01 — VERIFY RUNTIME TRUTH

## Goal
Establish the factual runtime state of AK BERMET before new production functionality.

## Repository
`/home/agent/projects/ak-bermet`

## Base
`develop`

## Work type
Verification and minimal test infrastructure only.
Do not implement housekeeping/technician UI in this task.
Do not deploy production.

## Required checks

1. Record exact `develop` commit SHA and clean status.
2. Confirm lint, TypeScript and production build.
3. Identify all Supabase migrations in order.
4. Determine whether a staging Supabase project is configured without printing secrets.
5. Read migration ledger.
6. Execute clean reset/apply on staging or local Supabase.
7. Confirm `btree_gist`.
8. Test role matrix:
   - anon;
   - owner;
   - administrator;
   - manager;
   - housekeeping;
   - technician;
   - inactive;
   - soft-deleted;
   - removed role.
9. Test:
   - direct manager API;
   - housekeeping cross-assignment;
   - technician cross-assignment;
   - history triggers;
   - RPC grants;
   - booking overlap;
   - simultaneous holds;
   - operational state transitions;
   - forbidden ready transitions.
10. Check stale README PIN reference and fix documentation only if present.
11. Store test code without real secrets or PII.
12. Produce evidence table:
   - check;
   - command/test;
   - result;
   - evidence;
   - status.
13. If credentials/environment are missing, stop with `BLOCKED_MISSING_ACCESS`; do not fabricate PASS.
14. No production deployment, no real-data import, no merge.

## PASS criteria

- migrations cleanly apply;
- no Critical/High authorization finding;
- role matrix passes;
- inactive/deleted users denied;
- direct API denied correctly;
- overlap protection works under concurrency;
- status transition rules enforced;
- build checks pass;
- secrets scan passes.

## Output
Report for independent Codex audit.
