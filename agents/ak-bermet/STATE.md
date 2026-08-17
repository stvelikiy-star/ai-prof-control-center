# AK BERMET — CURRENT STATE

Дата фиксации: 2026-08-17

## APPROVED

- AK BERMET — полноценный production-проект SPA & WELLNESS.
- GitHub `main` — source of truth для текущего кода.
- GPT = управляющий агент/архитектор/PM.
- Codex = основной технический исполнитель и независимый аудитор.
- AI PROF / Control Center = оркестрация задач и operations.
- Claude в текущем рабочем контуре AK BERMET не используется.
- Owner подключается только к бизнесовым, legal, price, real-data, secret и production gates.

## VERIFIED / PASS

- Кампания `AK_BERMET_3DAY_RELEASE_01` ранее завершена 9/9.
- Кампания `AK_BERMET_G00_REPAIR_01` ранее завершена 4/4.
- DEV staff auth runtime operation для 17 утверждённых сотрудников слита в Control Center через PR #77.
- 17 staff sessions были проверены реальным DEV UAT: 17 slots, 85 role checks, 8 policy checks — PASS.
- DEV role/RLS repair chain и audit-trigger security fixes подтверждались после последних миграций.
- Production structural preflight был доведён до PASS после настройки database URL contract; сам preflight не выполняет deploy/migration/write.

## CURRENT AGENT-AUTONOMY BLOCKERS

1. Project-agent instructions были устаревшими: Claude/develop/owner-on-every-cycle. Исправляются в V3.
2. AK BERMET generic task profile пока имеет `allow_commits=false`, `allow_push=false`, `allow_merge=false`.
3. Trusted `approved_task_publisher.py` сейчас KÖL-only; AK BERMET не умеет автоматически публиковать прошедший audit результат в PR.
4. Нужно подтвердить, что code execution path для AK BERMET использует Codex как основного исполнителя, а не Claude runner.
5. Нужен один живой E2E: Telegram `/ai task` → Codex change → required checks → independent audit → PR → Telegram terminal result.
6. Нужен fail/retry E2E, чтобы технический FAIL автоматически создавал ограниченный repair cycle без владельца.

## PRODUCTION / RELEASE REMAINS GATED

- Production target должен быть явно идентифицирован; DEV нельзя молча считать production.
- Backup/restore evidence должен быть завершён для фактического production target.
- Production migration/deploy/cutover требует отдельного owner approval.

## NEXT

Текущий приоритет — не новые продуктовые функции. Приоритет: закрыть автономный AI PROF delivery loop для AK BERMET в течение одного рабочего цикла, затем использовать его для оставшихся release задач.
