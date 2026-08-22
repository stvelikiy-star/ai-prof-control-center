# RESORT OS — CONTEXT

Resort OS is a universal hospitality/resort operating system intended to grow from Guest House / small property workflows toward hotel/resort/multi-property capability without fragmenting into separate products.

Core product areas include PMS/reservations/stays, guests, folio/charges/payments, housekeeping, maintenance, service/resource operations, Guest QR/mobile web, Partner/Agent relations, dashboards, integrations, and an AI Administrator operating only through controlled tools/domain services.

The current repository is intentionally a clean bootstrap baseline. Do not infer existing product capability from product intent.

Key preserved product constraints:

- one codebase / modular architecture direction;
- Reservation Request -> Check/Calculation -> Human Confirmation -> Confirmed Reservation;
- human final reservation confirmation;
- `AI_PERMISSION <= CURRENT_USER_PERMISSION`;
- no unrestricted production DB access for AI;
- deterministic critical logic outside LLM;
- Partner/Agent policy must not be silently changed;
- payments required as a product capability, implementation still validate;
- Current State factual truth belongs only to `knowledge/04_CURRENT_STATE.md`.

Initial working objective: establish verified Current State/GAP for the clean bootstrap, then implement the first vertical slice with tests/evidence and update Current State only after verification.
