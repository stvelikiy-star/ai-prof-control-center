# AI PROF CENTER — first real-repository autonomous execution gate

Date: 2026-11-10 (Asia/Bishkek)

## Target and immutable approval

- Target repository: `stvelikiy-star/ai-prof-control-center` (not the pilot repository).
- Source owner issue: `https://github.com/stvelikiy-star/ai-prof-control-center/issues/216`.
- Source body SHA256: `ae8c1fee00529f6145fbf0d36014d7529a1bc1617b835aad464d182a7b7c466c`, independently verified through GitHub API.
- Integration base: `feature/ai-prof-attention-profile-20261110`, pinned SHA `946e8ffc0c0eb2697f5e01bdb999e820941a56d1`.
- Allowed changes: `orchestrator/autonomous_queue_snapshot.py`, `tests/test_autonomous_queue_snapshot.py` only.
- Action: one isolated Codex checkout -> independent Python tests -> scoped feature commit -> draft PR to pinned integration base -> owner Telegram; never merge/deploy.
- Scope of new work: read-only deterministic autonomous queue counts with refusal of symlink or unsafe filesystem entries. No production runtime, DB, secrets, KOL or MARINA changes.

## Safety implementation

- Main Gateway's exact owner-authored `AI-PROF-AUTO-V1` contract retains immutable profile validation and dedupe.
- Source issue is owner, state=open, exact title/marker/body SHA256; checked again before publication.
- Worker chooses target repo only from source-controlled profile registry (no issue-supplied shell path or arbitrary repository).
- Checkout must match exact Git remote, pinned base SHA, expected branch, and clean status.
- Only the two declared files may change or stage; the returned remote PR, SHA, base and filenames are revalidated through GitHub API.
- Existing single-flight/timeout, interrupted-blocked-without-replay, atomic queue files and resource guard retained.

## Validation

- Targeted test suite: 52/52 PASS.
- Cross-route pilot/real compatibility: 68/68 PASS.
- Full repository regression after isolated-host CI fixture fix: 669/669 PASS. Earlier 668/668 PASS prior to CI correction.
- GitHub CI exposed a legacy KÖL preflight test assumption about a local Ubuntu-only `.git` directory; test now verifies both host-present and CI-absent cases fail closed. No production preflight policy changed.
- `git diff --check` PASS.

## Current release gate

Code and tests are prepared. **Actual control-center repository E2E is NOT accepted until a distinct main owner task is submitted after controlled Gateway reload and a scoped GitHub Draft PR, SHA, Telegram and queue completion are verified.** Off-host recovery and long burn-in remain separate blockers for unattended general rollout.
