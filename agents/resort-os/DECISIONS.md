# RESORT OS — DECISIONS

## D-001 — Clean bootstrap repository

A complete prior Guest House / Resort OS codebase was not recovered from the inspected local sources. Resort OS therefore starts from a clean private repository rather than pretending that the recovered PMS grid is a complete implementation.

## D-002 — Canonical governance preserved

The six canonical Knowledge documents remain the governing baseline. `04_CURRENT_STATE.md` is the only canonical owner of factual implementation reality. Statuses must not be upgraded without evidence.

## D-003 — Recovered PMS grid classification

`recovery-artifacts/pms-grid/PMSGrid.tsx` is retained as a historical/recovery UI prototype. It is not production truth and must not be used as evidence of a backend, booking engine, database, or live PMS.

## D-004 — Human reservation confirmation preserved

Reservation Request / Pending Draft may be created by AI/site/messengers, but final confirmation of a Confirmed Reservation remains a human-controlled action.

## D-005 — Controlled AI boundary preserved

AI is constrained by current-user permissions and controlled tool/domain API boundaries. No arbitrary production DB access. Critical deterministic logic must not depend on an LLM.

## D-006 — Payments status preserved

Payments remain an approved product requirement. Provider, legal availability, currencies, API route, and implementation remain `VALIDATE/UNKNOWN` until evidenced.

## D-007 — Safe AI PROF onboarding

Initial AI PROF registration grants no commit, push, merge, deployment, secret, or production authority. First task is a bounded bootstrap/current-state audit before broad implementation.
