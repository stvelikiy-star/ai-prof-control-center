# AI PROF FIFO activation — 14 November 2026 (Asia/Bishkek)

## Verified live host changes

Installed runtime SHA: `3c777887e0213f673ef30d8474df3a8efc9f9039`.
The established Gateway is startup-attested; services and the single-worker timer are active.
The read-only preflight returned ready=true with empty queues and passing resource/admission gates.
The established activation CLI completed legacy → frozen → FIFO, rollback to legacy, and a second activation to FIFO. No service restart, merge, deployment, database or MARINA change was performed.
The activation audit subsequently reports route=fifo and ready=false: its ready field means eligible to activate FROM legacy, not runtime health.
The existing timer worker completed an idle tick successfully. This is not evidence of a new real Codex task or delivery to Telegram.

## Regression and correction

The first complete run after activation found 15 legacy tests reading the live FIFO route (928 total). Their queue paths were isolated but route state and lock were not.
Both legacy fixture classes now use temporary MODE_FILE and ROUTE_LOCK paths. No production module was changed. The full rerun passed 928/928; git diff --check passed.
The earlier focused activation/FIFO/scheduler/route run passed 129/129.

## Remaining acceptance gates

- New approved owner task through the live FIFO → one agent execution → independent checks → remote Draft PR/SHA proof → Telegram terminal status. Synthetic tests do not replace this gate.
- General KÖL code intake still points at the historical dirty checkout and is not accepted for unattended execution. A separate clean checkout and adapter acceptance are required.
- FIFO currently supports immutable approved AI PROF profiles; it is not a universal all-project dispatcher.
- Disk monitoring is CRITICAL at 91% (about 11 GiB available); no project files, Docker volumes or caches were deleted during this change.
- Local encrypted backup is present; off-host recovery is unverified.

Decision: bounded FIFO route activation and rollback PASS; general unattended autonomy remains NOT ACCEPTED.
