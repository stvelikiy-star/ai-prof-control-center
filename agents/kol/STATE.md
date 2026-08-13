# KÖL CURRENT STATE

Updated: 2026-08-13.

## Verified

- Canonical local path: `/home/agent/Загрузки/kol-travel-platform`.
- Private GitHub repository: `stvelikiy-star/kol-travel-platform`.
- Recovered source baseline on `main`: `7e713b19f6c73c329c09df1163afba17c5443096`.
- KÖL is registered in AI PROF as `kol-travel-platform`.
- Source TypeScript and production build passed after the Phase 3 source patch; Next build completed 134/134 static pages.
- With `DATA_SOURCE_MODE=supabase` and `AUTH_PROTECTION_ENABLED=true`, unauthenticated `/client`, `/partner`, `/courier`, `/admin` redirect to `/login`.
- Database was not persistently changed by the Phase 3 source patch.

## Blocked / pending

- Persistent Auth/RLS repair remains blocked until a verified database backup exists.
- Seeded Auth rows are not confirmed sign-in-ready real accounts.
- Historical Stage 21 migration remains HOLD / not applied.
- Courier assignment model has known drift and is not part of the first Auth/RLS repair.
- Production deployment is not authorized by normal AI PROF code tasks.

## Current execution objective

Finish the recovered existing architecture in controlled phases. First remove infrastructure blockers in AI PROF remote execution, then continue Auth/RLS completion without mixing unrelated Stage 21, payments, courier redesign, alcohol, or deployment changes.
