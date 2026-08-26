# RESORT OS — STATE

Date: 2026-08-26

## VERIFIED / PASS

- Private GitHub repository: `stvelikiy-star/resort-os`.
- Local project path contract: `/home/agent/projects/resort-os`.
- Baseline branch: `main`.
- Current canonical GitHub `main`: `95cdee93d976862b6b928b7bbdb28dc7ef53ce2a`.
- Current executable/security baseline inside that history: `97f69cb5c091b49650bfa4b80beb095def75886b`.
- Canonical factual implementation owner is `knowledge/04_CURRENT_STATE.md` Version 1.4 on current main.
- The current executable/security baseline upgrades Admin/Public Web/Staff to Next.js 15.5.24 and React/React DOM 19.2.8 while preserving the trusted manifest fail-closed contract.
- The later `95cdee93...` commit is Knowledge-only Current State synchronization; GitHub Actions did not trigger workflows for that docs-only path, so executable verification remains anchored to the already-verified `97f69cb5...` security baseline rather than being falsely reclassified as a new code test run.
- Three Crowns clean-database migration baseline gate was merged and proven on clean PostgreSQL.
- Canonical repository contains Admin, Public Web, Staff, Resort Core/API, database/migration, PMS, booking and payment-domain implementation surfaces.
- Resort Core CI has exercised schema setup, application typecheck/build, Core compilation, seed, availability/request flow, auth/PMS, quote, manual-payment reservation flow, idempotency, PMS reflection, and check-in/checkout/housekeeping lifecycle.
- Public-site recovery was merged after removing unverified public amenity claims and preserving Resort Core availability/pricing plus the ReservationRequest boundary.
- Repository-owned AI PROF Stage 01B verification is `npm test` from the repository root.
- The root verification contract typechecks and builds Admin/Public/Staff and compiles Core/scripts Python.
- The trusted root verifier checks exact Git blob identities of all three app `package.json` manifests before executing their scripts and fails closed on manifest drift.
- Current trusted app manifest blobs are admin `e29254cc30c879d2e581db42002367a30d850bf7`, web `abe10c8520756ae0863702f2389bda821a956384`, staff `85e54aabc4afefc58d1d20b2a92031c4c364a1fa`.
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

Control Center may enable bounded Resort OS code tasks only with this exact required check and existing fail-closed scope/branch/authority controls. A stale local clone that is not synchronized to current canonical `main = 95cdee93d976862b6b928b7bbdb28dc7ef53ce2a`, or that does not contain the trusted root verification contract, must fail rather than silently substitute another check.

## NEXT SAFE MILESTONE

1. synchronize the local Resort OS clone to current canonical `main = 95cdee93d976862b6b928b7bbdb28dc7ef53ce2a` before any new code task;
2. start only bounded gap-driven tasks;
3. require root `npm test` for every code task;
4. keep production deployment, DB mutation, payment activation, secrets, and irreversible operations behind explicit owner gates;
5. update canonical `knowledge/04_CURRENT_STATE.md` only from verified implementation/runtime evidence.
