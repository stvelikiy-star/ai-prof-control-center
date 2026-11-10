# AI PROF — durable authorized cross-project FIFO, Phase 2 (integration pending)

## Purpose

Enable a single durable ordering surface for already-reviewed tasks of different enabled projects. This component is deliberately **metadata only**: it never executes arbitrary commands, accesses customer repositories, publishes changes, or grants agent rights.

## Exact contract

- `submit(project_id, task_id, contract_sha256, owner_verify=trusted_callback)`: checks explicit enabled registry state, restricted task identifier, 64-character immutable SHA-256 and a caller-provided review verifier. The verifier must validate the actual owner approval and repository/scope/policy in the existing gateway; an arbitrary callable is **not sufficient authority by itself**, so the FIFO is **not** exposed as a standalone API or Telegram command.
- The FIFO directory is a private AI PROF control-plane state path under `queue/global-agent-fifo-v1` and therefore included in existing encrypted local control-plane snapshots; never stores task instructions, credentials, arbitrary shell strings or customer files.
- A single exclusive mutex orders enqueues and dequeues. Sequence names are strictly increasing across reboots even if completed tickets have been retained or deleted. Duplicate `(project, task)` submissions are idempotent; changed digest is rejected.
- `claim` looks only at the oldest pending ticket and must first acquire the common cooperative execution lease. It does not skip the head when its project is unavailable. It never launches an agent.
- `finish` requires the trusted caller's verification result to mark the ticket completed; otherwise blocked. A process crash leaves an active ticket. `recover` refuses while the execution lock is held and, when safe, blocks interrupted tickets without retrying any agent.

## Activation prerequisites

1. Integrate the **existing owner-bound gateway verification** as a trusted `owner_verify` adapter for each project, with immutable scope and per-project allowed operation profile. Never treat enabled projects as permission to execute arbitrary tasks.
2. Convert the existing authorized launchers to submit and claim from this one FIFO. Confirm exact task ids, queue migrations, and rollback with isolated copies, no live queue edits.
3. Preserve real Codex/process checks, resource gates, bounded timeouts, inherited lock fd, Telegram result checkpoints, and no-replay remote evidence.
4. Execute cross-project E2E using synthetic tasks in isolated worktrees and verify ordered execution, interrupted recovery and no changes to customer projects.
5. Enable FIFO consumer on Ubuntu only after full CI, supervised rollout and rollback gate.

## Status at creation

- Unit tests for FIFO: 14 PASS. Full suite: 713 PASS.
- **NOT ACTIVATED**. Neither existing AI PROF queue nor customer launchers consume FIFO tickets yet. Cross-project order therefore is not yet guaranteed in production, despite the active global single-flight admission guard in AI PROF.
- No external backup change, no secret access, no merge, no deployment.
