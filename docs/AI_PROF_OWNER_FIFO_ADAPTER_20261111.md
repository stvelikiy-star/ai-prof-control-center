# AI PROF — owner-bound bridge to durable cross-project FIFO (Phase 3)

## Status: trusted adapter staged, live queue migration NOT enabled

A fixed-purpose bridge is now available for AI PROF GitHub autonomous task contracts. It does not create additional permissions: the existing `gateway_autonomous_intake.validate_contract`, `autonomous_shared_queue_worker.check_job` and GitHub issue identity/pilot-body digest checks remain authoritative.

The adapter accepts only an exact `AI_PROF_AUTO_MAIN_<positive issue number>` identifier already present as **one and only one** validated legacy `autonomous_pending` record. It rejects symlinks, changed owner/issue body, replaced pilot issue, wrong profile or SHA, active, completed, blocked or duplicate legacy records. The ticket contains only a project ID, task ID and pinned SHA; no instructions or credentials.

`stage_isolated` requires a distinct temporary legacy queue and distinct FIFO destination; both live roots are explicitly forbidden. It independently rechecks the remote Issue/pilot proof immediately before staging. `recheck_before_execution` is the mandatory second verification for a future executor: the same legacy pending record must still exist and match the ticket's SHA and project. The adapter's fixed project ID is `ai-prof-control-center`. Unknown projects and other enabled projects (including KÖL) are not automatically authorized through this adapter.

## Verified

- 21 targeted adapter tests PASS, covering owner impersonation, stale or future-dated pending entries, closed and altered issue, fake SHA, changed pilot issue, replayed completed/blocked/active tasks, duplicates, symlinks, unavailable GitHub and live destination rejection.
- Full local regression 734/734 PASS; syntax and diff checks PASS.
- Direct read-only Ubuntu evidence: historical completed autonomous Issue #218 and #215 correctly rejected as ineligible for migration.
- No real GitHub task submitted, no Codex executed, no live FIFO tickets, no client projects changed.

## Remaining work before production FIFO execution

1. Add owner-bound adapters for every additional explicit project and its registered task/repository profile, rather than accepting a generic truthy callback as authority.
2. Design an **atomic migration and exclusive intake handoff** between the legacy queue and FIFO. Never leave the same task executable by both routes; safe fallback is legacy unchanged until the takeover is verified.
3. Revalidate owner issue and immutable record at claim and again before publication; use remote PR/SHA evidence to reconcile interrupted jobs. Preserve no replay.
4. Run isolated cross-project end-to-end tests, test crash boundaries (before/after move, child exit, systemd restarts), and verify Telegram status and backup/restore with real evidence.
5. Only then enable a bounded FIFO consumer; keep single worker and existing resource gates. Merge, deployment, and customer repository writes remain owner-controlled and out of scope.

SWAP is near full on the 7 GiB Ubuntu host, so avoid parallel Codex launches during rollout.
