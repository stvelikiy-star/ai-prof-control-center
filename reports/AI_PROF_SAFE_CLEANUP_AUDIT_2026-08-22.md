# AI PROF SAFE CLEANUP AUDIT — 2026-08-22

Status: EVIDENCE-BASED PARTIAL AUDIT
Scope: GitHub-visible source/runtime relationships plus live evidence collected on 2026-08-22.
Rule: no file is safe to delete merely because its name/version looks old.

## Executive conclusion

AI PROF core is operational. A live safe task reached `approved` through Stage 01A, Stage 01B implementation/checks and independent Stage 01C audit. Telegram Bot API identity is valid and the dedicated Telegram service is active. The highest-value cleanup is therefore stale runtime state and incomplete project scaffolding, not aggressive source deletion.

Current recommendation: keep the runtime source tree intact; archive stale runtime/history conservatively; remove nothing from the active dependency chain until a host-side dependency/runtime audit proves it unused.

## Proven active/required dependency chain

### KEEP — active entrypoints/adapters

- `orchestrator/control_loop_service.py` — active systemd entrypoint observed on host. It imports `control_loop`, disables embedded Telegram supervision, upgrades Stage 01B to the V2 Codex adapter, and inserts the KÖL + AK BERMET publisher gates.
- `orchestrator/telegram_bridge_v4.py` — active dedicated Telegram service entrypoint observed on host.
- `orchestrator/github_task_gateway_service.py` — active dedicated GitHub gateway service observed on host.
- `orchestrator/codex_stage01b_runner_v2.py` — selected by `control_loop_service.py` for live Stage 01B execution.
- `orchestrator/codex_runner.py` — Stage 01C independent read-only auditor; live E2E evidence returned `STAGE_01C_AUDIT_PASS`.
- `orchestrator/approved_task_publisher_gate.py` — invoked before/after the core pipeline for KÖL.
- `orchestrator/ak_bermet_approved_task_publisher_gate.py` — invoked before/after the core pipeline for AK BERMET.

### LEGACY_BUT_REQUIRED — do not delete

- `orchestrator/control_loop.py` — imported and patched by `control_loop_service.py`; still owns the core supervisor/cycle implementation.
- `orchestrator/telegram_bridge.py` — imported as `legacy` by V3/V4 and also reused by the GitHub gateway. Removing it breaks the current Telegram/Gateway stack.
- `orchestrator/telegram_bridge_v2.py` — imported by V3; V4 delegates to V3; therefore V2 remains in the current call chain.
- `orchestrator/telegram_bridge_v3.py` — imported by V4 and supplies the terminal-result watcher/poll loop.
- `orchestrator/codex_stage01b_runner.py` — imported by `codex_stage01b_runner_v2.py`; V2 is an adapter, not a replacement core.
- `orchestrator/claude_runner.py` — despite Claude not being the current primary implementation model, `codex_stage01b_runner.py` imports it as the hardened Stage 01B core for isolation, scope validation, checks, patch application and rollback. Deleting it breaks current Codex implementation execution.

## KEEP — current safety/configuration files

- `orchestrator/projects.json` — current project registry and allowlist/forbidden-scope authority.
- `orchestrator/project_registry.py` — fail-closed branch/project policy helper.
- `agents/ai-prof-control-center/SYSTEM_INSTRUCTIONS.md`
- `agents/ai-prof-control-center/SOURCE_POLICY.md`
- `agents/ai-prof-control-center/STATE.md`
- `agents/ai-prof-control-center/APPROVAL_MATRIX.md`
- `agents/ai-prof-control-center/DECISIONS.md`

The five AI PROF Control Center context files were confirmed present and non-empty on the live host.

## ARCHIVE_CANDIDATE — conservative only

### Incomplete pilot scaffold

`agents/ai-prof-pilot/` currently contains only `CONTEXT.md`, while the hardened Stage 01B context loader expects the five canonical context files. A live pilot task failed before model execution with `Required context file missing or empty: .../agents/ai-prof-pilot/SYSTEM_INSTRUCTIONS.md`.

Recommendation: disable/remove the `ai-prof-pilot` registry entry only after deciding the pilot is retired; then archive the pilot directory. Do not delete only the directory while the registry still references it.

### Historical runtime queues/logs

Host evidence on 2026-08-22 showed:
- blocked: 27
- failed: 16+ historical entries
- completed: 26
- one stale `active` AK BERMET task from 2026-07-27 with no references found in the checked runtime/run/campaign/log paths.

Recommendation: export/archive terminal history and the stale orphan task into a timestamped archive outside active queue paths. Do not mass-delete queue history before preserving evidence.

### Historical project task/report documents

Repository `tasks/AK_BERMET_*` and older reports are candidates for a documentation archive, not deletion, because they preserve security/recovery evidence. They do not currently justify runtime removal by themselves.

## DELETE_CANDIDATE

None proven safe from current evidence.

Reason: several files that look obsolete by version/name are direct dependencies of the active runtime. Source deletion should require a second host-side import/entrypoint scan plus tests after any proposed consolidation.

## STALE/BROKEN findings

1. Stale active task: `AK_BERMET_20260727T172909Z_86110D` dated 2026-07-27 remained under `queue/active` with no references found in the sampled run/campaign/log paths.
2. `ai-prof-pilot` context is incomplete for the current hardened runner and should not be used for smoke testing until repaired or retired.
3. A self-maintenance live test on 2026-08-22 intentionally modified `README.md` in the maintenance worktree and reached `approved`. Because self-maintenance has no equivalent trusted publisher in the observed live service, the maintenance worktree may remain dirty after that approved test. This is a likely blocker for subsequent `require_clean_repository` tasks and must be checked locally before re-running cleanup tasks.
4. Telegram historical logs contain repeated `Telegram API request failed`, but current Bot API `getMe` and `getWebhookInfo` succeeded, webhook is unset, and pending update count was zero. Treat the historical polling error as requiring current service verification, not as proof the token is invalid.

## Safe cleanup order

1. Host-side verify the self-maintenance worktree branch/status and restore only the known README smoke-test diff if it is the sole expected change.
2. Re-run a bounded AI PROF self-maintenance task to prove clean-repository intake/Stage 01B/Stage 01C after restoration.
3. Archive the stale July `active` task with evidence; do not silently delete it.
4. Decide whether `ai-prof-pilot` is retired. If yes: remove/disable registry entry in a reviewed change, then archive pilot context/runtime project.
5. Archive old terminal queue/log history by retention policy.
6. Only after a complete import/entrypoint scan and green tests consider consolidating versioned adapters. Current evidence says V2/V3/V4 and legacy cores are required.
7. Register Resort OS as a new isolated project only after the Control Center maintenance worktree is clean and the post-cleanup E2E is green.

## Blockers requiring local-host evidence

- exact current `git status` and diff of `/home/agent/projects/ai-prof-control-center-maintenance`;
- exact terminal reason stored for blocked task `AI_PROF_CONTROL_CENTER_20260822T054950Z_0A81AD`;
- safe archival/move of the stale runtime `active` task;
- current Telegram `/ai status` response from the live bridge.

These are local runtime facts and cannot be certified from the GitHub repository alone.

## Final classification

Core runtime source deletion: DO NOT START.
Runtime/history cleanup: READY AFTER ONE LOCAL READ/REPAIR GATE.
Resort OS onboarding: NEXT AFTER CLEAN POST-CLEANUP E2E PASS.
