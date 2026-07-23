# AK BERMET — STAGED DELIVERY ROADMAP

## Phase 00 — Project Agent installation
Goal: установить пакет знаний в Control Center.
Gate: Codex PASS.
Production changes: none.

## Phase 01 — Runtime truth verification
Goal:
- определить фактическое состояние `develop`;
- определить staging Supabase;
- получить migration ledger;
- выполнить clean apply;
- проверить btree_gist;
- выполнить role matrix;
- проверить direct API, triggers и overlap.

Gate:
- STAGING_PASS;
- нет Critical/High;
- отчёт с evidence.

## Phase 02 — Inventory and staff foundation
Goal:
- импортировать и сверить 169 units;
- создать роли и 17 тестовых пользователей;
- проверить ограничения видимости.

Gate:
- reconciliation = 169 units / 407 official / 484 max;
- role access PASS;
- секреты не раскрыты.

## Phase 03 — Housekeeping workspace
Goal:
- assignments;
- accept/start/complete;
- after photo;
- problem reporting;
- before/after photos;
- auto assignment + admin override.

Gate:
- role/RLS tests;
- mobile UAT;
- audit history.

## Phase 04 — Technician workspace
Goal:
- maintenance requests;
- diagnosis;
- work/materials;
- result photo;
- blocked/maintenance states.

Gate:
- status/RLS tests;
- mobile UAT;
- history.

## Phase 05 — Administrator inspection
Goal:
- inspection after complaint/problem/repair;
- approve/reject;
- ready only after required checks.

Gate:
- forbidden transitions rejected;
- audit trail complete.

## Phase 06 — Booking, hold and availability integrity
Goal:
- 60-minute hold;
- expiry;
- transaction/concurrency;
- no overlap;
- blocked rooms excluded.

Gate:
- parallel tests PASS;
- rollback PASS.

## Phase 07 — Payments
Goal:
- amount, method, date, balance, manager confirmation;
- link to booking;
- audit.

Gate:
- reconciliation and permissions PASS.

## Phase 08 — Current booking migration
Goal:
- mapping;
- dry-run;
- duplicate handling;
- reconciliation;
- rollback;
- owner-approved cutover.

Gate:
- owner approval;
- backup;
- zero unresolved blocking discrepancies.

## Phase 09 — Integrations
Goal:
- Google Sheets reporting/export;
- n8n;
- Telegram Control Center;
- AI assistant with restricted permissions.

Gate:
- secrets isolated;
- retries/idempotency;
- failure notifications.

## Phase 10 — UAT and production launch
Goal:
- end-to-end UAT;
- security regression;
- backup/restore;
- observability;
- production checklist.

Gate:
- owner approval;
- Codex PASS;
- rollback ready;
- launch report.
