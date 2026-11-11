# AI PROF CENTER — safe start for the shared autonomous Codex queue

## Scope

The existing owner-authorized single-flight queue, exact GitHub contract, fixed project/repository, PR and Telegram reporting, and no-replay interrupted recovery remain unchanged. This patch only strengthens the **pending -> active** launch gate in `autonomous_shared_queue_worker.py`.

Before launching a new Codex worker, in addition to the existing RAM/disk checks, inspect `/proc/meminfo`, `/proc/pressure/memory`, and executable names of running `/proc/<pid>/cmdline` records:

- If an existing Codex `exec` process is detected, defer with `RESOURCE_DEFERRED_OTHER_CODEX_ACTIVE`. No action on that other process, including MARINA or other clients.
- If the already-reviewed swap pressure classifier reports CRITICAL, defer with `RESOURCE_DEFERRED_SWAP_MEMORY_PRESSURE`.
- Missing or invalid core memory metrics fail closed for new launches. A disappearing process PID is handled safely.
- Old swap occupancy alone, with healthy RAM/PSI, warns via the independent burn-in monitor but does not automatically disable all work.

The pending record remains unchanged, preserving no-retry semantics and allowing the normal timer to retry the **resource check**, not replay a Codex attempt. This is cooperative admission control, not an OS-wide atomic lock; unrelated agents that do not use the gate may still start after the check. Global cross-project isolation requires a common coordinator and owner review before activation.

## Validation

- Targeted tests: 38 PASS; full regression: 681 PASS; `git diff --check`: PASS.
- Feature branch and Draft PR only. The live AI PROF Gateway/worker services were not restarted or upgraded, and no client project was changed.
- User project policy: AI PROF only. No changes to MARINA SMART, KÖL, databases, credentials, merge/deploy or remote backups.
