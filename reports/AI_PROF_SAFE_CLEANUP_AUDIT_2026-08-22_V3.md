# AI PROF Safe Cleanup Audit — 2026-08-22 V3

## Audit boundary

This is a documentation-only, conservative cleanup audit for task `AI_PROF_CONTROL_CENTER_20260822T055516Z_11BED5`, sourced from authorized private GitHub task issue #96. The only implementation evidence reviewed was:

- `orchestrator/telegram_bridge.py`
- `orchestrator/telegram_bridge_v2.py`
- `orchestrator/github_task_gateway.py`

No implementation, runtime, state, queue, service, systemd, secret, production, or database changes are authorized or recommended by action in this audit. A classification describes cleanup disposition only; it does not prove that a component is currently deployed or activated.

## Classification summary

| File | Classification | Cleanup disposition |
| --- | --- | --- |
| `orchestrator/telegram_bridge.py` | **LEGACY_BUT_REQUIRED** | Retain. It is a shared security and execution dependency of both other scoped files. |
| `orchestrator/telegram_bridge_v2.py` | **KEEP** | Retain. It is an executable V2 control-plane extension and depends on the original bridge. |
| `orchestrator/github_task_gateway.py` | **KEEP** | Retain. It is an executable, fail-closed GitHub-to-task gateway and depends on the original bridge. |

There are no `ARCHIVE_CANDIDATE` or `DELETE_CANDIDATE` results within the scoped evidence.

## Direct code evidence

### `orchestrator/telegram_bridge.py` — LEGACY_BUT_REQUIRED

- The module is a complete executable Telegram polling bridge: `run()` continuously polls Telegram and checkpoints offsets before handling side effects (lines 653–673), while `main()` configures redacted logging and starts that loop (lines 676–691).
- It enforces owner-and-chat authorization in `authorized()` (lines 335–345), loads only an explicit configuration-key allowlist and validates the bot token (lines 302–332), and selects task scope exclusively from the project allowlist (lines 412–443).
- It invokes task intake with an argument vector and `shell=False` (lines 463–477 and 550–567), so it contains active safety and compatibility behavior rather than inert duplicate code.
- V2 explicitly imports this module as `legacy` (in `telegram_bridge_v2.py`, line 18), reuses its state and project paths (lines 20–22), and delegates authorization, queue inspection, redaction, Telegram transport, base command handling, and lifecycle to it throughout the file, including lines 288–334.
- The GitHub gateway also imports it (in `github_task_gateway.py`, line 30), uses its redactor as the shared secret-detection boundary (lines 85–99), and later uses its queue/status helpers (lines 421–428).

The `legacy` alias and V2 extension structure support the legacy label, but the direct imports and extensive reuse make archive or deletion unsafe. This file remains required unless all callers are migrated and independently validated under a separately approved change.

### `orchestrator/telegram_bridge_v2.py` — KEEP

- The module identifies itself as the Telegram Control Plane V2 and explicitly excludes arbitrary shell execution, secrets, destructive Git operations, migrations, and deployment (lines 2–7).
- Its help contract exposes owner-facing diagnostics and task, queue, log, blocker, Git-status, and release-preparation commands while restating the safety limits (lines 27–51).
- Diagnostic Git calls are fixed argument arrays and set `GIT_OPTIONAL_LOCKS=0` (lines 72–85); the implemented Git command set is read-only status/revision inspection (lines 156–187).
- `extended_handle_update()` first applies the original owner/chat authorization, recognizes a bounded set of commands, and delegates unmatched/base commands to the original handler (lines 292–328).
- `main()` installs the extended handler and then invokes the original bridge lifecycle (lines 331–338), making this an executable extension rather than an unused helper module.

No scoped code proves that a replacement provides the same V2 behavior. The file must therefore be retained.

### `orchestrator/github_task_gateway.py` — KEEP

- The module documents a deliberately narrow private-GitHub-issue-to-validated-task gateway, including fixed ownership, strict JSON, downstream validation, non-execution of issue prose, denied destructive authority, and crash-safe deduplication (lines 2–13).
- Repository and owner trust anchors are fixed in code (lines 43–46); accepted priorities and actions are allowlisted, while commit, push, merge, deployment, secrets, and destructive operations are required forbidden values (lines 48–78).
- Contract parsing requires the title/body markers, exact contract keys, version 1, a safe project token, a matching issue title, an allowed priority, supported actions, and preservation of the forbidden-action boundary (lines 111–181).
- Authorization accepts only fixed owner logins and rejects pull requests (lines 184–191). Task submission uses an argument vector to `submit_task.py` and verifies that intake created a pending task (lines 281–331).
- Queue reconciliation blocks on multiple matches rather than guessing (lines 334–379), state is persisted with restrictive permissions and atomic replacement (lines 382–418), and the executable polling loop uses a single-instance lock (lines 529–601).
- It directly depends on `telegram_bridge.py` for redaction and queue/status interpretation (lines 30, 85–99, and 421–428).

This is active gateway and safety-boundary code with no scoped replacement evidence. It must be retained.

## Unscoped files

All unscoped files are **UNKNOWN**. In particular, every unscoped runtime file, service or systemd file, Codex-related file, Telegram V3 file, Telegram V4 file, and any other V3/V4 control-plane file remains **UNKNOWN and must not be deleted, renamed, moved, or archived** based on this audit.

No inference about activation, obsolescence, duplication, or cleanup safety may be drawn from filenames, version labels, task history, or the three scoped modules alone. Establishing those facts requires a separately authorized inventory and reference review.

## Safe cleanup order

1. **Retain all three scoped files now.** There is no approved deletion or archive candidate in this audit.
2. **Keep every unscoped file UNKNOWN.** Do not act on runtime, service, Codex, Telegram V3/V4, or other unreviewed files.
3. **Obtain a separately scoped, owner-approved dependency inventory.** It must cover service entry points, imports, tests, operational launch configuration, documentation, and rollback requirements without changing runtime state.
4. **Prove replacement parity before considering legacy cleanup.** Any proposed successor to `telegram_bridge.py` must replace its consumers and preserve authorization, redaction, task-scope selection, queue/status handling, offset deduplication, and V2/GitHub-gateway compatibility.
5. **Validate in an isolated checkout.** Run the applicable tests and security checks after an explicitly scoped migration; do not weaken fail-closed behavior or validation.
6. **Archive before delete, only under a new owner-approved task.** Use a reversible, bounded archive step after references and rollback are verified. Observe the resulting system under the separately approved operational process.
7. **Delete only after a further explicit approval.** Deletion remains report-only here and must be backed by evidence of zero references, replacement parity, successful validation, an elapsed rollback window, and owner authorization.

## Blockers

- The audit scope contains no service definitions, runtime launch configuration, tests, import inventory, or deployment evidence, so actual activation and complete reference graphs cannot be established.
- `telegram_bridge_v2.py` and `github_task_gateway.py` directly depend on `telegram_bridge.py`; removing the original bridge would break scoped imports and shared safety behavior.
- No scoped Telegram V3/V4 or other successor implementation was available for parity review. All such files remain UNKNOWN.
- Runtime, state, queue, systemd, secrets, production, and databases are explicitly outside authorization, so no live-use or inactivity claim can be verified here.
- Existing implementation files are evidence-only and may not be modified, renamed, or deleted under this task.
- Any archive or deletion requires a new bounded task and explicit owner approval. Urgency and the cleanup label do not supply destructive authority.

## Audit conclusion

The only conservative result supported by the scoped code is to retain all three files: `telegram_bridge.py` is **LEGACY_BUT_REQUIRED**, while `telegram_bridge_v2.py` and `github_task_gateway.py` are **KEEP**. No deletion is safe from the available evidence. All unscoped files remain **UNKNOWN** and must not be deleted.
