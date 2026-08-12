# Source Policy

Priority for self-maintenance decisions:
1. Current validated GitHub `main` of `stvelikiy-star/ai-prof-control-center`.
2. Explicit owner-approved task contract.
3. Existing automated tests and fail-closed security invariants.
4. This agent context.

Do not infer production permissions, credentials, deployment targets, or destructive authority from old task history. Missing authority means blocked/owner action required, not permission by assumption.
