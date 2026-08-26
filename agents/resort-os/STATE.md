# RESORT OS — STATE

Date: 2026-08-26

## VERIFIED / PASS

- Private GitHub repository: `stvelikiy-star/resort-os`.
- Local project path contract: `/home/agent/projects/resort-os`.
- Baseline branch: `main`.
- Three Crowns clean-database migration baseline gate was merged and proven on clean PostgreSQL.
- Canonical repository contains Admin, Public Web, Staff, Resort Core/API, database/migration, PMS, booking and payment-domain implementation surfaces.
- Resort Core CI has exercised schema setup, application typecheck/build, Core compilation, seed, availability/request flow, auth/PMS, quote, manual-payment reservation flow, idempotency, PMS reflection, and check-in/checkout/housekeeping lifecycle.
- Public-site recovery was merged after removing unverified public amenity claims and preserving Resort Core availability/pricing plus the ReservationRequest boundary.
- Repository-owned AI PROF Stage 01B verification is `npm test` from the repository root.
- The root verification contract typechecks and builds Admin/Public/Staff and compiles Core/scripts Python.
- The trusted root verifier checks exact Git blob identities of all three app `package.json` manifests before executing their scripts and fails closed on manifest drift.
- The verification gate and trusted runner are outside ordinary AI PROF Resort OS task scope.
- Normal AI PROF Resort OS tasks retain no commit, push, merge, deployment, secret, production, or destructive database authority.

## CURRENT IMPLEMENTATION REALITY

Resort OS is no longer only a clean bootstrap baseline. A substantial canonical implementation is present and has repository-level test/build evidence.

This evidence does **not** by itself prove a live production deployment, a production database cutover, a live payment provider, or production guest traffic. Those statuses remain separate gates and must not be inferred from CI.

Current repository behavior must preserve:

- Reservation Request / Pending Draft -> Check / Calculation -> Human Confirmation -> Confirmed Reservation;
- human final reservation confirmation;
- `AI_PERMISSION <= CURRENT_USER_PERMISSION`;
- controlled domain/API tools rather than unrestricted production DB access;
- deterministic critical business logic outside the LLM;
- fail-closed verification and idempotency boundaries.

## VALIDATE / UNKNOWN

Unless independently evidenced by current production/runtime inspection:

- production deployment target and live deployment status;
- production database migration/cutover state;
- payment provider and legal activation route;
- live external-channel integrations;
- exact V1 product scope where canonical Knowledge still marks it `VALIDATE`;
- first ICP validation;
- any production SLA/traffic/financial claim not directly evidenced.

## AI PROF EXECUTION STATUS

The previous monorepo-check quarantine has a verified repository-owned replacement: root `npm test`.

Control Center may enable bounded Resort OS code tasks only with this exact required check and existing fail-closed scope/branch/authority controls. A stale local clone that does not contain the root verification contract must fail rather than silently substitute another check; synchronize the local `main` before the first Resort OS code task.

## NEXT SAFE MILESTONE

After Control Center registry enablement is merged and its own CI is green:

1. synchronize the local Resort OS clone to the verified canonical `main`;
2. start only bounded gap-driven tasks;
3. require root `npm test` for every code task;
4. keep production deployment, DB mutation, payment activation, secrets, and irreversible operations behind explicit owner gates;
5. update canonical `knowledge/04_CURRENT_STATE.md` only from verified implementation/runtime evidence.
