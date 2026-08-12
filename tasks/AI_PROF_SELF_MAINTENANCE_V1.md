# AI PROF SELF-MAINTENANCE V1

## Objective
Turn AI PROF Control Center into a safely self-maintainable system so the owner can submit/manage work from ChatGPT mobile and Telegram, while keeping production/destructive actions owner-gated.

## Current proven blockers
- Control Center config has `allow_merge: false`.
- Control Center config has `allow_production_deploy: false`.
- Auto-repair policy leaves security/infrastructure blockers blocked.
- Only AK BERMET is currently represented in the project registry/context directory; Control Center itself is not a managed project.
- Telegram Control Plane V2 exists as draft PR #2 but is not activated.
- ChatGPT Control Gateway V1 is specified in GitHub issue #3 but not implemented.

## Required implementation

### A. Self-maintenance project profile
1. Add `ai-prof-control-center` as an explicit managed project with local path `/home/agent/projects/ai-prof-control-center` and GitHub repo `stvelikiy-star/ai-prof-control-center`.
2. Create a dedicated self-maintenance operation profile. It must be stricter than normal project profiles.
3. Self-maintenance may edit code/tests/docs on a dedicated work branch only.
4. It must never directly overwrite the currently running checkout/service files without a staged activation step.
5. Require backup of changed service/orchestrator files before activation.
6. Require full Control Center test suite PASS before activation.
7. Require rollback instructions and preserved previous working version.
8. No force push, destructive git cleanup, secret printing, arbitrary shell from Telegram/issue text, or production customer data mutation.

### B. Telegram Control Plane V2 completion
1. Rebase/port PR #2 cleanly onto current Control Center `main` rather than the historical intermediate base.
2. Preserve owner-only authorization.
3. Support and test `/ai health`, `/ai queue [project]`, `/ai task <TASK_ID>`, `/ai logs <TASK_ID>`, `/ai blockers <project>`, `/ai git <project> status`.
4. Preserve existing `/ai status`, `/ai task ...`, `/ai release <project> prepare` behavior.
5. Run old + new Telegram tests.
6. Provide staged systemd activation and rollback.

### C. ChatGPT Control Gateway V1
Implement GitHub issue #3 exactly:
- ChatGPT/GPT -> private GitHub task inbox -> Control Center importer -> existing validated queue -> agents/Codex -> state callbacks to issue + Telegram.
- Owner allowlist only.
- Strict structured task schema.
- Deduplication.
- No secrets.
- No arbitrary shell.
- Existing validator cannot be bypassed.

### D. Owner-gated production runner
1. Keep production disabled by default.
2. Add explicit owner approval token/action for an exact project + exact release SHA + exact action set.
3. Approval must be single-use, short-lived, auditable, and revocable.
4. Allowed production sequence after approval: frozen SHA verification -> fresh backup verification -> migration-ledger check -> apply only missing migrations if any -> deploy to verified target -> smoke -> evidence.
5. If deployment target is not proven, stop OWNER_ACTION_REQUIRED rather than guessing.
6. Never run `supabase db reset`, blind migration replay, force push, or destructive data operations.

### E. AK BERMET handoff after platform repair
After A-D PASS and activation:
1. Do not repeat already proven AK BERMET gates.
2. Recognize as existing evidence: current release CI/security PASS, Supabase project identity PASS, migration ledger 18/18, fresh DB dump roles/schema/data + SHA256 PASS, Google Sheets backup present.
3. Create one new final AK BERMET task that continues only from remaining production gates.
4. Finish with `PRODUCTION PASS` or one exact `OWNER_ACTION_REQUIRED` blocker.

## Acceptance criteria
- Control Center can receive a task for its own code through the self-maintenance profile without scope-blocking.
- Telegram V2 is tested and active.
- ChatGPT Gateway imports one authorized GitHub task exactly once and reports its Task-ID/status.
- Owner can approve one bounded production release from Telegram/Control Gateway without globally enabling deploy.
- A dry-run/self-test proves forbidden actions remain blocked.
- Existing AK BERMET autonomous queue behavior remains compatible.

## Safety
No production deployment is authorized by this task itself. This task only builds/tests the mechanisms and activation procedure. Production approval remains a separate owner action.
