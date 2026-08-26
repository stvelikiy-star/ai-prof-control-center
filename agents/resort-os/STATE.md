# RESORT OS — STATE

Date: 2026-08-26

## VERIFIED / PASS

- Private GitHub repository: `stvelikiy-star/resort-os`.
- Local project path contract: `/home/agent/projects/resort-os`.
- Baseline branch: `main`.
- Current canonical GitHub `main`: `d19a235f8c471913561f1aae1c6d2860653c64d0`.
- Current executable baseline inside that history: `f68e2ff6428929f4e069d650ff2b8d30a6224599`.
- Canonical factual implementation owner is `knowledge/04_CURRENT_STATE.md` Version 1.6 on current main.
- `d19a235f...` is a docs-only Current State synchronization commit; executable verification remains anchored to `f68e2ff...` rather than being falsely reclassified as a new code test run.
- Exact executable main `f68e2ff...` produced 14 push-triggered GitHub Actions runs and the same exact-SHA query filtered to `status=success` returned 14; post-merge result is 14/14 SUCCESS.
- The current executable history includes the patched Next.js 15.5.24 / React 19.2.8 application baseline while preserving the trusted manifest fail-closed contract.
- Three Crowns clean-database migration baseline gate was merged and proven on clean PostgreSQL.
- Canonical repository contains Admin, Public Web, Staff, Resort Core/API, database/migration, PMS, booking and payment-domain implementation surfaces.
- Resort Core CI has exercised schema setup, application typecheck/build, Core compilation, seed, availability/request flow, auth/PMS, quote, manual-payment reservation flow, idempotency, PMS reflection, and check-in/checkout/housekeeping lifecycle.
- Public site has a centralized 12-category room catalog, `/rooms` plus 12 category pages, canonical homepage, repository-local rendered media, Resort Core-backed availability/request flow and vendor-neutral public booking-funnel analytics.
- Public analytics uses event-specific payload types plus runtime allowlists; guest name, phone, email, free-text notes, request IDs and exact travel dates are excluded from the permitted event payloads.
- `scripts/public_site_truth_guard.py` fails closed on stale fixed-prepayment rules, conference/billiards/laundry/sauna CURRENT claims, conference-media promotion in protected public files, remote/hotlinked media, missing live Core booking endpoints, missing request-not-booking wording, category-count drift, or sensitive keys being added to the public analytics allowlist.
- `Public Site Truth CI` passed on exact executable main `f68e2ff6428929f4e069d650ff2b8d30a6224599`.
- Admin contains a standalone `/demo` route with explicitly synthetic presentation data; it does not write Resort Core/PostgreSQL and does not replace the authenticated Core-backed production admin surface.
- Resort Core exposes protected read-only `/api/v1/automation/read/crm-feed` mirror data for ReservationRequest, Reservation and Payment records; Resort Core remains the source of truth.
- An importable Google Sheets CRM n8n workflow exists in the repository and is committed inactive with no OAuth/service secrets.
- Repository-owned AI PROF Stage 01B verification is `npm test` from the repository root.
- The root verification contract typechecks and builds Admin/Public/Staff and compiles Core/scripts Python.
- The trusted root verifier checks exact Git blob identities of all three app `package.json` manifests before executing their scripts and fails closed on manifest drift.
- Current trusted app manifest blobs remain admin `e29254cc30c879d2e581db42002367a30d850bf7`, web `abe10c8520756ae0863702f2389bda821a956384`, staff `85e54aabc4afefc58d1d20b2a92031c4c364a1fa`.
- The verification gate and trusted runner are outside ordinary AI PROF Resort OS task scope.
- Normal AI PROF Resort OS tasks retain no commit, push, merge, deployment, secret, production, or destructive database authority.

## CURRENT IMPLEMENTATION REALITY

Resort OS has a substantial canonical implementation with repository-level test/build evidence. The current executable baseline contains the bounded public-site/privacy truth guard, a read-only CRM mirror contract, an isolated presentation-only Admin demo, and privacy-safe public funnel analytics.

Repository evidence does **not** by itself prove a live production deployment, production database cutover, live payment provider, live Google Sheets synchronization, an external analytics vendor, production guest traffic, or exact Vercel deployment/source correspondence. Those statuses remain separate gates and must not be inferred from CI.

Current repository behavior must preserve:

- Reservation Request / Pending Draft -> Check / Calculation -> Human Confirmation -> Confirmed Reservation;
- human final reservation confirmation;
- `AI_PERMISSION <= CURRENT_USER_PERMISSION`;
- controlled domain/API tools rather than unrestricted production DB access;
- deterministic critical business logic outside the LLM;
- Resort Core as booking/payment/inventory source of truth;
- CRM/Google Sheets as mirrors only unless canonical authority explicitly changes that design;
- Admin `/demo` as synthetic presentation-only data, never production truth;
- fail-closed verification, public-truth/privacy and idempotency boundaries.

## VALIDATE / UNKNOWN

Unless independently evidenced by current production/runtime inspection:

- exact production deployment target/source correspondence and live deployment status;
- production database migration/cutover state;
- payment provider and legal activation route;
- live external-channel integrations;
- Google Sheets OAuth binding, n8n workflow publication and live CRM mirror synchronization;
- external GTM/GA/other analytics vendor activation and live event delivery;
- final category-specific media pack and production visual acceptance;
- exact V1 product scope where canonical Knowledge still marks it `VALIDATE`;
- first ICP validation;
- any production SLA/traffic/financial claim not directly evidenced.

## AI PROF EXECUTION STATUS

The previous monorepo-check quarantine has a verified repository-owned replacement: root `npm test`.

Control Center may enable bounded Resort OS code tasks only with this exact required check and existing fail-closed scope/branch/authority controls. A stale local clone that is not synchronized to current canonical `main = d19a235f8c471913561f1aae1c6d2860653c64d0`, or that does not contain executable baseline `f68e2ff6428929f4e069d650ff2b8d30a6224599` and the trusted root verification contract, must fail rather than silently substitute another check.

## NEXT SAFE MILESTONE

1. synchronize the local Resort OS clone to current canonical `main = d19a235f8c471913561f1aae1c6d2860653c64d0` before any new code task;
2. start only bounded gap-driven tasks;
3. require root `npm test` for every code task and preserve Public Site Truth CI for public-site/privacy changes;
4. keep production deployment, DB mutation, payment activation, secrets, Google OAuth/publish activation, external analytics activation and irreversible operations behind their required gates;
5. treat the committed Google Sheets workflow as inactive until credentials/publication/live sync are independently verified;
6. treat Admin `/demo` only as a synthetic presentation surface, never production evidence;
7. update canonical `knowledge/04_CURRENT_STATE.md` only from verified implementation/runtime evidence.
