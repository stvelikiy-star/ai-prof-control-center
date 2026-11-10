# AI PROF — реальная интеграция основного GitHub Gateway и автономной очереди

Дата: 10 ноября 2026 года, 18:55–19:30 Asia/Bishkek.

## Подтверждённый цикл

- Основной приватный репозиторий владельца: `stvelikiy-star/ai-prof-control-center`.
- Источник: GitHub issue №211 — `https://github.com/stvelikiy-star/ai-prof-control-center/issues/211`.
- Контракт: `AI-PROF-AUTO-V1`, профиль `pilot-task-counts-v1`, точное имя, автор `stvelikiy-star`, запрет свободных команд/доступа к произвольному проекту, SHA256 тела пилотного issue №7.
- Действующая системная служба `ai-prof-github-task-gateway.service` импортировала №211 **самостоятельно** в основной `STATE_ROOT/queue/autonomous_pending/AI_PROF_AUTO_MAIN_211.json`; основной state сохранил `autonomous_queued`.
- Отдельный ограниченный user timer `ai-prof-autonomous-shared-queue.timer` автоматически переместил задание в `autonomous_active` в 18:57:09.
- Codex реализовал `summarize_task_counts` в отдельном приватном тестовом репозитории `stvelikiy-star/ai-prof-autonomy-pilot`; изменены только `scripts/task_health.py` и `tests/test_task_health.py`.
- Проверки независимого исполнителя PASS; Git commit `c7d0541cbfc54f5cfff32122b02779a59377147b`.
- Draft PR №8: `https://github.com/stvelikiy-star/ai-prof-autonomy-pilot/pull/8`; open, draft=true, merged=false, base=main, changed_files=2; GitHub SHA совпадает.
- Telegram результат от самого исполнителя: message №586; `notified=true`.
- Основная очередь: `queue/autonomous_completed/AI_PROF_AUTO_MAIN_211.json` имеется, в `autonomous_active` и `autonomous_pending` записи №211 нет.
- Следующее срабатывание user-service вернуло `AI_PROF_AUTO_LANE_IDLE`; повторный Codex, PR и Telegram не создавались.
- Исходный GitHub issue №211 получил комментарий `AI-PROF-AUTO-RESULT-V1 issue=211` с результатом PASS, точным SHA, PR и фактом доставки Telegram. Главный state сохранил `autonomous_completed`, `result_reported=true`; повторные опросы не создают повторного комментария.

## Надёжность и границы

- В основной GitHub Gateway исправлена необработанная ошибка `subprocess.TimeoutExpired` при `gh api`, ранее вызвавшая 21 systemd restart. После перезапуска PID сменился, служба active и `NRestarts=0`.
- В отдельной автоматической полосе нет произвольного shell-выполнения текста issue: фиксированы владелец, репозиторий, профиль, путь к проекту, разрешённые файлы, SHA256 контракта и формат ответа.
- Файлы очереди атомарно создаются с правами 0600. Общая файловая блокировка `fcntl.flock`; не больше одного active задания. После остановки в процессе публикации — только remote-верификация или BLOCKED, без повторного выполнения.
- User service ограничена `MemoryMax=1800M`, `TimeoutStartSec=600s`; worker process group ограничена 420 секундами и принудительно завершается при timeout. Есть RAM/disk guards.
- Перед staging, push и PR исполнитель заново проверяет действительность исходного issue, включая открытый статус и неизменность контракта. Закрытие/редактирование владельцем блокирует публикацию.
- Никаких изменений в KÖL, MARINA SMART, основной базе или production; merge/deploy запрещены.
- Внесённые тесты на авторизацию, отзыв полномочий, дедупликацию и аварийное восстановление; полная регрессия AI PROF: **662/662 PASS**.

## Честные границы приёмки

**PASS**: одна реальная owner-approved задача через основной системный GitHub Gateway → общая изолированная очередь → Codex → тесты → Draft PR → Telegram → исходный GitHub issue. Службы работают после перезапуска.

**НЕ ПРИНЯТО**: общий исполнитель для разных реальных проектов, произвольных разрешённых задач и множественных независимых профилей; длительная обработка многих новых задач, автоматическое разрешение конфликтов, полностью безручное восстановление зависших операций после аварии. Сейчас разрешён ровно один фиксированный пилотный профиль. Расширение требует новых профилей и отдельного аудита прав.

## Состояние таймера

`ai-prof-autonomous-shared-queue.timer` можно оставить включённым: при пустой очереди он отвечает `IDLE`, новых профилей самостоятельно не добавляет, уже завершённое issue не повторяет.
