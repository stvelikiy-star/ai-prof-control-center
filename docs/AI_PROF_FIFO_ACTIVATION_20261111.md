# AI PROF — controlled activation and rollback of the owner-only FIFO lane

## Read-only by default

`python3 scripts/ai_prof_fifo_activation.py` produces a JSON readiness report. It does not modify routes, tasks, repositories, services or deployment state.

Readiness requires a currently running, startup-pinned and code-hash verified GitHub Gateway, active established Gateway service and autonomous timer, both queues empty under the existing locks, the RAM/disk guard, the independent Codex and memory-pressure guard, and a confirmed exact local SHA.

## Activation

The owner-Gateway must first be restarted by an authorized system administrator using the established system service controls. Never force-kill a Gateway, run a second poller or change a live route while the old service process is active.

If the independent readiness report says ready=true, an authorized operator may invoke:

`python3 scripts/ai_prof_fifo_activation.py --activate --expected-sha <reviewed 40-char SHA> --confirm AI_PROF_OWNER_FIFO_ONLY`

The script verifies the SHA and all conditions, freezes the legacy route atomically, then enters FIFO only if the queue is still idle under the authoritative worker lock, global execution lease and Gateway route gate. If phase two fails, it attempts to restore legacy. If restoration is unsafe it leaves the system frozen, not executing a second route.

## Rollback

After the FIFO route has no pending or active work, invoke:

`python3 scripts/ai_prof_fifo_activation.py --rollback --expected-sha <reviewed SHA> --confirm AI_PROF_OWNER_FIFO_ONLY`

This passes through frozen and refuses stale Gateway attestations or work in progress. It does not replay completed or blocked work.

## Strict scope

The FIFO implementation currently handles only exact owner-approved AI PROF autonomous Issue profiles. It is NOT a universal execution path for MARINA SMART, KOL or other clients. Those client repositories and independent processes are unchanged. This remains a Draft PR and does not grant merge, deployment, secrets, or publishing rights beyond existing approved contracts.

Validation: 14 dedicated acceptance tests PASS; 810/810 full suite PASS. No real Codex jobs were used in activation tests.
