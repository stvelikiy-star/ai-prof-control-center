# AI PROF — bounded FIFO runtime for exact GitHub owner profiles, phase 6

## What is implemented

An **explicit opt-in** FIFO execution route is now implemented for the existing and already-reviewed `AI-PROF-AUTO-V1` owner Issue profiles only. It is not a generic executor for all projects.

- `atomic_route_guard` now understands `legacy`, `frozen`, and `fifo`. Direct `legacy <-> fifo` is rejected; activation requires an explicit `frozen` intermediary while the old worker lock, global lease and route lock are held and both queues have no pending/active tickets.
- The GitHub owner intake has unchanged `legacy` semantics and, only in `fifo`, creates the existing immutable `autonomous_pending` evidence first, then stages one matching metadata-only FIFO ticket with exact owner/pilot issue verification. A crash between the two is safely reconciled on the next poll under the FIFO mode; the legacy worker will not execute it. No new project/path/profile/action authority is introduced.
- Under the existing one-worker timer and global lease, FIFO picks only the oldest ticket, rejects unsupported project heads, rechecks owner/pilot GitHub evidence **at claim time**, moves the original legacy ticket to active and uses the same bounded Codex subprocess and independent PR/SHA/Telegram proof as the original worker. It updates both ledgers on completion or blocks the task without auto-retry.
- An active FIFO ticket on the next tick is only reconciled against independent published evidence. If the child still owns the global lease, the ticket is deferred; it is never replayed. A legacy active ticket with no corresponding FIFO active ticket blocks all further FIFO jobs pending review.
- No customer repository or secret is read by FIFO beyond the original fixed owner authorization workflow. Merge, automatic deployment and unrestricted shell are not enabled.

## Tested

- Existing route/role/queue/Telegram integration: full local suite **789/789 PASS**, including targeted 42 route + FIFO-runtime scenarios.
- Synthesized GitHub owner/pilot contracts with temporary queue roots, mocked Codex, and a real Linux global lease; successful path, failed remote proof, crash boundaries, wrong project, stale child lock, duplicate intake and owner-policy changes. No real customer job executed.
- `git diff --check` PASS.

## Operational activation gate — not yet met

**This branch MUST NOT be switched to `fifo` until both the systemd Gateway and the existing user-timer worker are independently confirmed to have loaded this exact reviewed SHA.** An outdated Gateway would bypass the new route, enabling duplicate paths.

After CI and code-only local installation, a privileged restart of `ai-prof-github-task-gateway.service` may be needed, as the Remote Desktop Commander user cannot safely restart this system service. No unapproved forced termination or replacement polling process should be used.

Only after signed-off process versions, exact queue quiescence, and an isolation/recovery review may a controlled `legacy -> frozen -> fifo` be considered. Revert via `fifo -> frozen -> legacy` only when both queues have no pending or active work. The existing owner Issue statuses and GitHub callbacks must be checked during the cutover.

All other clients (KÖL, MARINA SMART, etc.) remain on their existing workflows. The FIFO runtime does **not** make their independent processes globally serialized. Separate owner-bound adapters are needed for each allowed project, and independent terminal Codex agents remain outside the cooperative guarantee.

No external backup changes, no GitHub merge, no deployment.
