# AK BERMET autonomy blocker — 2026-08-12

## Observed runtime status
- AI PROF Bridge: healthy
- Control Center: healthy
- `AK_BERMET_20260811T123222Z_E38EA5`: failed
- `AK_BERMET_20260811T141428Z_0D2B84`: blocked
- completed campaigns remain 9/9 and 4/4

## Proven architectural blocker
Current Control Center policy remains fail-closed for production:
- global `allow_merge = false`
- global `allow_production_deploy = false`
- AK BERMET project profile `allow_push = false`
- AK BERMET project profile `allow_merge = false`
- AK BERMET project profile `allow_deployment = false`
- security/infrastructure blockers remain blocked under auto-repair policy

Therefore a task whose acceptance criterion is full production completion cannot reach `PRODUCTION PASS` under the current operation profile, even when application code/release checks have passed.

## Required remediation
Do not create more duplicate AK BERMET finalization tasks until AI PROF self-maintenance and a bounded owner-gated production runner are implemented and activated.
