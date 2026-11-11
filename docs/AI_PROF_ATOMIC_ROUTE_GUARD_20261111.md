# AI PROF — atomic legacy intake/worker freeze gate, phase 5

## Scope

For the **existing owner-authorized AI PROF GitHub autonomous lane only**, both the Gateway intake and queue worker now consult one OS `flock` route gate. Missing mode file means `legacy` so existing deployments do not change behavior. The supported explicitly persisted modes are `legacy` and `frozen`. There is **no `fifo` route** and no arbitrary execution path.

- Legacy Gateway `process()` acquires the route lock around the existing established owner and pilot contract handling, and if frozen returns a nonterminal DEFER without writing task state or queue entries. It resumes on thaw; no rejected-state or phantom duplicate is created.
- Existing worker `tick()` checks route lock inside its existing single-flight `RUN_LOCK` and returns a nonterminal deferred status if frozen or unverified. This stops **new** work but does not kill or replay a started worker. Existing resource and global single-flight gates, remote evidence and no-replay remain unchanged.
- `atomic_route_guard.transition()` is an opt-in administrative library function: acquire the **existing worker RUN lock** (nonblocking), then the **shared global execution lease**, then the **same Gateway route lock**, check that both queues have no pending or active tasks, then atomically persist a 0600 mode record with file+directory fsync. It supports only `legacy -> frozen` and `frozen -> legacy`. Missing worker lock, unsafe path, unknown mode, queue work or lock contention fails closed.
- The route lock and mode file reside in the private AI PROF control-plane run directory. No secrets, customer tasks, project repositories, deployment state or GitHub PRs are changed by freeze.

## Live activation restriction

**DO NOT set `frozen` on a host until the running Gateway and worker have both been restarted or otherwise attested to use this exact SHA.** A gateway still running old code will ignore the new route lock, so setting frozen prematurely would be unsafe. Installing the code without setting the mode is backward compatible.

**This is not yet FIFO activation.** The next reviewed implementation must route owner-verified new tickets through the global FIFO, consume under the global lease with full crash recovery, preserve gateway status and Telegram callbacks, and adopt other projects only via separately trusted adapters. Client tasks may not be migrated or reexecuted just because the cutover snapshot was empty.

## Evidence

- Route-boundary, forged-mode, disabled-mode, busy-worker, global-agent contention, no duplicate, no task mutation, gateway defer/thaw and worker defer: 21 targeted PASS.
- Full local regression: 768/768 PASS; `git diff --check` PASS.
- No running systemd services restarted or mode files created in production during this phase. All tests used isolated temporary queue and lock directories.
