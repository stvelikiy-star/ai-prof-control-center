# AI PROF — приёмка автоматического пилотного Gateway

Дата: 10 ноября 2026 года, Asia/Bishkek.

## Что фактически произошло

- Приватный GitHub репозиторий: `stvelikiy-star/ai-prof-autonomy-pilot`.
- Новый owner-authored issue: №3, `AI-PROF-PILOT-E2E-V2`; точный текст закреплён SHA256 `d2c2bd217bc842d1d5039e376afea3100657d83a9afcbb97cae9b8ce2b849d96`.
- Изолированный checkout: `/home/agent/projects/ai-prof-pilot-auto-gateway`; исходный SHA main `91e573c26468f3112d1dabf23e4420621241d914`.
- `ai-prof-pilot-autonomous-gateway.timer` самостоятельно запустил ограниченный worker в 14:24:03, без ручной команды `codex`.
- Codex добавил `summarize_task_json` в `scripts/task_health.py` и тесты в `tests/test_task_health.py`; дополнительные файлы не изменял.
- Независимые тесты Python 12/12 PASS, дополнительные независимые проверки JSON-семантики 6/6 PASS.
- Git commit `6b800b75fbe4e31070a6828fe391118b2df6d346`, SHA совпал с GitHub.
- Draft PR №4: `https://github.com/stvelikiy-star/ai-prof-autonomy-pilot/pull/4`, base main, scope ровно два файла, merged=false.
- Telegram сообщение №582; сохранены `notified=true` и идентификатор сообщения в state.
- Повторные systemd tick: `PILOT_GATEWAY_ALREADY_COMPLETED_NO_DUPLICATE`; второй PR/Telegram не создавались.
- Все три основных сервиса AI PROF active, NRestarts=0. KÖL/production/MARINA SMART не изменялись.

## Отказ первой автоматической попытки и исправление

- Первый таймер запустился в 14:21, но git SSH завершился code 128: `PrivateTmp=true` несовместим с Ubuntu systemd SSH proxy конфигурацией.
- Отказ произошёл до работы Codex. Локальный Git остался на чистой develop, удалённой feature-ветки не появилось.
- Проблема воспроизведена отдельно через `systemd-run --user ... -p PrivateTmp=yes git ls-remote`, а `PrivateTmp=no` прошло.
- ТОЛЬКО у ограниченного пилотного user-service установлено `PrivateTmp=false`; главный GitHub Gateway и Control Center не менялись.
- Исходный state неудачной попытки сохранён как `pilot-issue-3-e2e-20261110-first-failed-git128.json`; затем разрешена одна повторная попытка.

## Защита

- Контракт owner-only, точный issue №3 и SHA256 body, неизменные paths из двух разрешённых файлов.
- Один checkout, одна ветка, фиксированный base commit, запрет повторного запуска при failed/partial/terminal state.
- Resource gate RAM ≥1.5 GB, disk <88%. Codex ограничен 240 секундами, worker systemd — 420 секундами.
- Только git push scoped feature branch и Draft PR. Merge, deployment, базы данных и остальные проекты запрещены.
- Telegram только после успешной приёмки; защита от повторной отправки.
- Отдельный user systemd service сохраняет необходимые SSH-proxy настройки без изменения системных root-служб.

## Проверки

- 10 адресных unit-тестов защищённого gateway.
- Полная регрессия AI PROF по 625 тестов, повторить последовательно 10 раз и зафиксировать в эксплуатационном отчёте.
- Верификация GitHub PR, локального Git, трассы systemd, SHA, scope, тестов, уведомления.
- Необходимо отдельно проверить, что одноразовый timer отключён после приёмки, чтобы не потреблять ресурсы.

## Границы результата

**PASS**: реальная задача из GitHub автоматически обработана по systemd timer → Codex → тесты → commit/push → Draft PR → Telegram. Первая неудачная попытка документирована, причина устранена.

**НЕ ДОКАЗАНО**: произвольная непрерывная много-issue очередь из основного Gateway, автономная 2–4-часовая разработка для всех проектов, CI для тестового репозитория (отдельный GitHub Actions не настраивался), устойчивость к перезагрузке посреди Codex и восстановление в середине Git push. Эти пункты не следует объявлять PASS.

Следующий этап: отдельно включить ограниченный реестр утверждённых задач в едином Gateway; только после дополнительных аварийных тестов — управляемая очередь 2–4-часовых заданий.

## Повторная регрессия и остановка пилота

- 10/10 последовательных независимых полных запусков unittest: **625/625 PASS в каждом** (всего 6250 тестовых выполнений), 10 ноября 14:27–14:29.
- Изолированный однократный user timer **disabled/inactive** после PASS; он не будет продолжать опрос завершённого пилотного issue.
- Программные компоненты и настройка пилотного gateway сохранены для последующей интеграции в основной много-issue контур.
