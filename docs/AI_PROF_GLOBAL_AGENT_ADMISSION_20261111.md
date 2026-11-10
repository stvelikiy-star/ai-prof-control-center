# AI PROF — shared agent execution lease (Phase 1)

## What this patch guarantees

- A single Linux `flock(LOCK_EX | LOCK_NB)` for **cooperating** agent runners across explicitly enabled projects from `orchestrator/projects.json`.
- Fail-closed for disabled, unknown or malformed project registrations; unsafe symlink or permissive lock files rejected; no secrets, arbitrary commands or customer project data read.
- Reuses owner-reviewed task validation; this lease grants **zero** authority to modify a project, commit, publish, merge or deploy.
- AI PROF's current GitHub autonomous queue takes the shared lease before its resource gate, immutable GitHub contract check and pending-to-active transition. On contention it remains `pending` and preserves attempt budget.
- Lease fd is inherited by the bounded worker subprocess, so the lock survives premature **parent** exit while that worker remains alive; lease closes on normal completion, error and ordinary process exit. The existing crash reconciliation is still no-replay, remote-proof only.
- The existing `/proc` Codex detection and RAM/disk/PSI/SWAP admission remain enabled under the lease.

## Non-goals and cautions

This patch does **not** yet route all projects' jobs through a single durable FIFO. The current autonomous queue is FIFO within its own approved task class; the global lock is a **cooperative, non-FIFO, single-flight execution slot**. It does not intercept Codex started in independent terminals, other users' systemd units or separate project tooling that has not opted in. No guarantees of atomic isolation against those launches are claimed. No other project's launchers or repositories were changed.

The inherited file descriptor remains with the bounded worker while it lives. A further orphaned Codex grandchild after the worker dies is handled by the existing live process preflight at the next attempt; this is **not** an unconditional guarantee against an OS-wide race. A later reviewed phase should connect the legacy Stage 01B/01C runners, add a durable authorization-bound cross-project FIFO, and then activate only the safe common route after acceptance.

## Integration contract for an approved future runner

1. The runner first validates its own immutable owner-reviewed task and exact registered `project_id`.
2. Import `global_agent_admission.acquire(project_id)`. `None` means **DEFER**, not error/retry of an already-running task; `AdmissionError` means **BLOCKED/NOT VERIFIED**.
3. Hold the returned lease throughout resource checks, task transition and full subprocess execution. For worker subprocesses use `pass_fds=(lease.fd,)`, retaining the fd in the worker process while any child agent is alive.
4. Release via `with lease:` or `lease.close()` after completion, then reconcile interrupted jobs from remote proof without replay.
5. Never merge, deploy, push or change scope because the lock was granted. Those capabilities remain separate owner permissions.

## Acceptance evidence

- Targeted cross-process and queue tests: 42/42 PASS.
- Full test suite on isolated branch: 699/699 PASS; `git diff --check` PASS.
- No service restart, runtime installation or customer project changes; draft PR only until CI and monitored acceptance.
