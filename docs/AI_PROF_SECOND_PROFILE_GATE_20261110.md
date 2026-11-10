# AI PROF CENTER — two consecutive autonomous issue executions, 2026-11-10

## Real end-to-end PASS

- The system GitHub Gateway was restarted on Ubuntu with current exact-contract profiles and remained active.
- Main owner issue #214 uses distinct source pilot issue #9 and approved `pilot-task-overview-v1`. Its queued task completed first. GitHub Draft PR #10 is open, unmerged, base `main`, SHA `4aa931fbf196a206db9d67de11405b15a5c24fd3`. Telegram message #590 delivered.
- Main owner issue #215 uses distinct source pilot issue #11 and approved `pilot-task-attention-v1`. Its task entered the queue only after #214 had completed, then completed. GitHub Draft PR #12 is open, unmerged, base `main`, SHA `a7a1440964a86843f797375268f141cb4dfc62c0`. Telegram message #591 delivered.
- For each PR, live GitHub API confirmed exactly two changed files: `scripts/task_health.py`, `tests/test_task_health.py`; expected remote head SHA; draft=true; merged=false.
- Queue artifacts #214 and #215 exist separately in `autonomous_completed`, and latest worker journal returned `AI_PROF_AUTO_LANE_IDLE` after #215. No merge or deployment.
- Owner contract for source issue #11 was pinned with live GitHub body SHA256 `ffbdabdc22825b2446453b1c13d3f94d736493510719f91355883693a39d64e3`.
- Full native regression after changes: 663/663 PASS. Focused queue/Gateway suite 47/47 PASS; `git diff --check` PASS.

## Isolated crash / reconciliation PASS

- Two disposable child processes exited with code 78 after moving a synthetic queue entry to active.
- With injected negative publication evidence, real queue `tick()` marked the entry blocked; with injected positive evidence, it marked the entry completed; neither re-ran Codex.
- These are isolated state-integration crash tests with mocked remote evidence. A real remote PR-verification crash test remains a separate release gate.

## Safety/release limitations

- GitHub issues #212 and #213 are historical closed failed attempts; #212 blocked as a duplicate and #213 rejected by a previously stale Gateway process. Neither is a successful acceptance task.
- Historical KOL V4 publication task is blocked due to missing explicit publication authority; no rights are added retroactively.
- Execution is still constrained to exact pilot profiles and an isolated private repo; general project rollout and production approval are not established.
- Off-host disaster recovery, extended burn-in and a multi-project permission audit remain unverified.

**Accepted: TWO DISTINCT AUTONOMOUS PILOT TASKS E2E PASS. Full commercial/production launch: NO-GO until remaining release gates.**
