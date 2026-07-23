# AK BERMET — OPEN RISKS

## R-01 — Live database not evidenced
Severity: Critical for launch
Status: OPEN
Нужны staging project reference, migration ledger и clean apply evidence.

## R-02 — Runtime RLS matrix not evidenced
Severity: Critical
Status: OPEN
Проверить anon и все роли, inactive, soft-deleted, role removal, cross-assignment, direct API.

## R-03 — Booking concurrency not evidenced
Severity: High
Status: OPEN
Проверить параллельные holds/bookings и exclusion/transaction behavior.

## R-04 — Operational workflows incomplete
Severity: High
Status: OPEN
Housekeeping и technician pages нельзя считать готовыми без реальных действий, assignment и фото.

## R-05 — Data migration not designed end-to-end
Severity: High
Status: OPEN
Нужны mapping, dry-run, reconciliation, duplicate policy, rollback и cutover plan.

## R-06 — Test users not created
Severity: Medium
Status: OPEN
Создавать только через безопасный Admin API/Dashboard; временные пароли не коммитить и не печатать в отчётах.

## R-07 — Telegram/n8n linkage not evidenced
Severity: Medium
Status: OPEN
Группа создана, но техническое подключение к Control Center должно быть проверено отдельно.

## R-08 — Stale documentation
Severity: Low
Status: VERIFY
Проверить README и удалить устаревшие упоминания `/manager/login` PIN.

## R-09 — Backup/restore not evidenced
Severity: High
Status: OPEN
Нужен backup, restore drill и rollback для миграции.
