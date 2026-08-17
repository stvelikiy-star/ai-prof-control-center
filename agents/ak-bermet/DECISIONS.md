# AK BERMET — DECISION LOG

## 2026-07-24 — Project classification
AK BERMET является полноценным production-проектом, не пилотом.

## 2026-07-24 — Operational scope
Строится полный цикл: бронирование → выезд → уборка → готовность.

## 2026-07-24 — Readiness authority
После обычной уборки горничная может завершить уборку и перевести номер в ready.
После жалобы, ремонта или найденной проблемы требуется проверка администратора.
AI не имеет права переводить номер в ready.

## 2026-07-24 — Cleaning assignment
Автоматическое распределение с возможностью ручного переназначения администратором.

## 2026-07-24 — Photo policy
Обычная уборка: фото после.
Проблема: фото до и после.

## 2026-07-24 — Maintenance blocking
Проблема, мешающая заселению, переводит номер в maintenance_required или blocked и исключает его из доступности до ремонта и проверки.

## 2026-07-24 — Booking hold
Удержание номера: 60 минут.

## 2026-07-24 — Initial staff
1 owner, 1 administrator, 4 managers, 6 housekeepers, 5 technicians.

## 2026-07-24 — Data migration
Актуальные бронирования переносятся в Supabase. После сверки Supabase становится источником истины.

## 2026-07-24 — Payment phase 1
Хранить сумму, способ, дату, остаток и подтверждение менеджера. Онлайн-оплату подключать позже.

## 2026-08-17 — Git source of truth
Текущий GitHub `main` и новые подтверждённые результаты имеют приоритет над историческими ветками, старым STATE и старым master context. `develop` не считать текущим source of truth без явной причины конкретной задачи.

## 2026-08-17 — Agent team
Текущий рабочий состав AK BERMET:
- GPT / ChatGPT — управляющий агент, архитектор, project manager;
- Codex — основной технический исполнитель и независимый аудитор;
- AI PROF / Control Center — оркестрация;
- GitHub — source of truth;
- Owner — business/legal/prices/real-data/secrets/production approval.

Claude в текущем рабочем контуре AK BERMET не использовать.

## 2026-08-17 — Autonomous technical authority
Технические изменения внутри уже утверждённого scope должны идти автономно: branch → Codex execution → required checks → independent Codex audit → repair if needed → trusted PR publication. Владелец не вызывается между техническими стадиями, если owner gate не пересечён.

## 2026-08-17 — Production boundary
Автономность технического delivery loop не означает production authority. Production migration/deploy/cutover, реальные данные и secrets остаются отдельными owner gates.
