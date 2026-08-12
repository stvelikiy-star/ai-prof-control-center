# KÖL — AI PROF operating context

Project ID: `kol-travel-platform`

Canonical local path: `/home/agent/Загрузки/kol-travel-platform`

Rules:

- Preserve the recovered KÖL architecture and current business model. Do not redesign the project by default.
- Treat the current exact source baseline as authoritative after the recovery bootstrap.
- Source work may use the existing AI PROF isolated Codex implementation/audit pipeline.
- Do not read, expose, edit, stage, or commit `.env*`, credentials, secrets, `node_modules`, `.next`, or Supabase temporary state.
- Do not commit, push, merge, deploy, mutate production, or apply database migrations from normal code tasks.
- Persistent database changes remain blocked until a verified database backup gate has passed.
- Do not apply the historical Stage 21 migration unless a later explicit owner-approved operation profile authorizes it.
- For source validation, prefer `npx tsc --noEmit` and `npm run build` unless the task contract requires a narrower safe check.
- If repository identity, branch, source scope, backup state, or production target is ambiguous, fail closed and report the blocker.
