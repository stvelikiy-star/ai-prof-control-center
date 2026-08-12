# AI PROF Control Center — main integration checkpoint

Date: 2026-08-12

## Proven branch state
- Base: `main`
- Integration head: `fix/control-center-local-migration-campaign-guard`
- Ahead: 69 commits
- Behind: 0 commits

## Integration scope
The integration branch contains the current operational AI PROF platform that is already used by the Ubuntu runtime, including:
- autonomous queue/orchestrator
- Codex runners
- auto-repair
- campaign runner
- control loop
- operation profiles and operations runner
- project registry
- release flow
- Telegram bridge
- systemd service definitions
- tests for the above

## Current known limitations to preserve during integration
- `allow_merge` remains false globally.
- `allow_production_deploy` remains false globally.
- AK BERMET project profile keeps push/merge/deployment disabled.
- security/infrastructure blockers remain fail-closed.
- Telegram Control Plane V2 remains a separate draft PR and must be ported/tested after this baseline reaches `main`.
- ChatGPT Control Gateway V1 is specified separately in issue #3.

## Required merge gate
Before merging integration branch to `main` on the Ubuntu host:
1. clean worktree
2. current Control Center service healthy
3. run full existing Python test suite
4. run Telegram bridge tests
5. run release-flow tests
6. confirm systemd unit tests
7. no secret values in output
8. create rollback tag/backup of current working checkout

No production customer deployment or data mutation is authorized by this integration checkpoint.
