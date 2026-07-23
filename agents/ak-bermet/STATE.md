# AK BERMET — CURRENT STATE

Дата фиксации: 2026-07-24

## APPROVED

- AK BERMET — полноценный production-проект.
- Целевая архитектура: единая CRM + Supabase/PostgreSQL.
- Полный цикл booking → checkout → cleaning → ready.
- Удержание: 60 минут.
- Правила уборки, фото, ремонта и проверки администратора.
- Состав первых пользователей.
- Перенос актуальных броней и последующий Supabase cutover.
- Первый этап учёта оплат.

## IMPLEMENTED / STATIC_PASS

- Supabase Auth и роли добавлены в код.
- Операционные миграции подготовлены.
- Security blockers из Audit 001 исправлены.
- Soft-deleted manager direct API defect исправлен.
- Targeted security re-audit: PASS.
- Изменения объединены в `develop`.
- Baseline lint, TypeScript и production build ранее проходили.

## UNKNOWN — обязательно проверить

- наличие и параметры staging Supabase;
- точный список применённых миграций;
- clean migration reset/apply;
- btree_gist;
- runtime RLS role matrix;
- runtime trigger behavior;
- runtime booking overlap/concurrency;
- импорт номерного фонда;
- создание 17 тестовых пользователей;
- реальные housekeeping/technician workflows;
- Telegram/n8n technical connection to Control Center;
- backup/restore evidence;
- production environment readiness.

## BLOCKED

Production deployment и миграция реальных данных заблокированы до завершения Phase 01 runtime verification.
