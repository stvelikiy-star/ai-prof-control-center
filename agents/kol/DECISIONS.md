# KÖL CONFIRMED DECISIONS

## Architecture

- Continue the recovered existing KÖL project; do not recreate or redesign it by default.
- Canonical business ownership key is `business_id = partners.id`; do not introduce a competing `partner_id` model.
- Categories use `category_id`; hierarchy uses `parent_id` / `scope`.
- `partner_profiles.business_id` and catalog `business_id` point to `partners.id`.
- Supabase/PostgreSQL remains the primary backend contour for the recovered architecture.

## Security and Auth

- Complete real server-side Auth/session/profile/role resolution before expanding unrelated functionality.
- Route protection applies to client, partner, courier and admin cabinets when real Supabase mode is enabled.
- Service-role access is server-only and must not be used as a browser/public-catalog shortcut.
- RLS/Auth repair must preserve least privilege and pass adversarial role checks.
- Persistent Auth/RLS SQL waits for a verified database backup.

## Scope ordering

- First: Auth/RLS repair and real scoped cabinets completion.
- Do not mix first repair with Stage 21, payments, courier redesign, AI expansion, alcohol, or production deploy.
- Stage 21 remains HOLD until separately authorized.
- Known courier assignment drift is tracked separately.

## Delivery discipline

- Normal AI PROF code tasks may edit/test only approved scope and may not commit, push, merge, deploy, access secrets or mutate production.
- Fail closed when repository identity, branch, backup state, DB target or destructive effect is uncertain.
