# AI PROF — read-only cross-queue cutover preflight (Phase 4)

This probe is **not** a switch, a migration, a background worker or authorization to execute. It takes a nonblocking exclusive `flock` on the already-existing legacy autonomous worker lock and the registered global execution lease, then counts entries in both legacy and FIFO `pending` and `active` directories. Any unsafe path, missing/unsafe lock, active job, pending job or busy lock blocks readiness.

- `QUIESCENT_SNAPSHOT_ONLY` proves only that the two queues were empty of executable work **while both inspected locks were held**. The locks are released on return, so new gateway intake could occur immediately afterwards. DO NOT interpret this response as durable cutover permission.
- `LEGACY_WORKER_BUSY`, `GLOBAL_AGENT_BUSY`, `ACTIVE_TASK_REQUIRES_RECOVERY`, `PENDING_TASK_REQUIRES_HANDOFF`, or filesystem error block cutover.
- The probe does not create lock files, start Codex, touch client repositories, mutate queue records, or enable the new FIFO consumer.
- A future actual cutover requires coordination with the GitHub task intake producer (shared drain/handoff under one transaction) plus a separate explicit owner-reviewed deployment gate. A reader-only probe cannot freeze intake.

## Evidence

- 13 targeted tests PASS: idle snapshot, active/pending legacy and FIFO, actual flock contention, symlink, lock permissions, and no worker execution or task mutation.
- Complete local regression: 747/747 PASS.
- Previous owner-bound adapter PR #226 independently passed GitHub CI. No live source migration.

Keep full single-worker pressure safeguards; Ubuntu SWAP is close to full.
