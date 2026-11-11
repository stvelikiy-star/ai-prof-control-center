# AI PROF — изолированный режим KÖL V4

Дата: 10 ноября 2026 года. Работающая KÖL и production не затрагиваются.

## Два отдельных профиля

- Основной KÖL: `kol-travel-platform` в `/home/agent/Загрузки/kol-travel-platform`, основа `integration/kol-unified-20261105`. Незавершённые файлы сохранены.
- Для автономного PR: `kol-v4-pr` в `/home/agent/projects/kol-ai-prof-v4-runner`, основа `main` на SHA `0a04ba6659da9b5b9b830aa301ab1c7dc684c27e`. Собственный .git. Remote: `git@github.com:stvelikiy-star/kol-travel-platform.git`.
- Для security PR №157: отдельная папка `/home/agent/projects/kol-ai-prof-pr-clone` и отдельная ветка `fix/kol-security-deps-20261110` с HEAD `3059f246`. Она не является рабочей копией автономного исполнителя и не должна использоваться для следующей задачи.
- Общие права commit/push/merge/deploy для обоих профилей выключены. Только проверенный отдельный контракт KÖL V4 вправе сделать один scoped commit/push/PR без merge/deploy.
- Первая область KÖL V4 ограничена `docs/**` и `scripts/**`.
- Issue №172 имеет статус orphaned и не подлежит автоматическому повторному созданию.

## Этапы допуска

1. `python3 -m unittest discover -s tests -p 'test_*.py' -q` — общий PASS.
2. `npm ci --ignore-scripts --no-audit --no-fund --prefer-offline` — только в изолированном клоне, без sudo. Проверить наличие tsc/next/eslint.
3. Применить ограниченный systemd drop-in к Control Center, разрешающий запись только в отдельный клон (привилегированная операция владельца Ubuntu).
4. Перезапустить только AI PROF Control Center и GitHub Gateway, проверить статус и отсутствие рестартов.
5. `python3 orchestrator/kol_v4_pr_preflight.py` — обязан показать `KOL_V4_PR_PREFLIGHT_PASS`. При BLOCKED новый GitHub issue не создавать.
6. Один новый owner-authored V4 issue с точной областью `scripts/check-deployment-env-selftest.mjs`; проверить 01A, 01B Codex, 01C Audit, PR, комментарий в issue и русское Telegram-уведомление. Без merge/deploy.
7. Только после E2E PASS — последовательные многочасовые задания с ограничениями RAM и времени.

## Команда владельца Ubuntu для разрешения рабочей папки

```bash
sudo install -d -m 755 /etc/systemd/system/ai-prof-control-center.service.d
sudo install -m 644 /home/agent/projects/ai-prof-control-center/ops/systemd/system/ai-prof-control-center.service.d/zzzz-kol-v4-clean-checkout.conf /etc/systemd/system/ai-prof-control-center.service.d/zzzz-kol-v4-clean-checkout.conf
sudo systemctl daemon-reload
sudo systemctl restart ai-prof-control-center.service ai-prof-github-task-gateway.service
```

Эта команда не выполняется автоматически; без разрешения сервиса задача запуска PR остаётся заблокированной.

## Итог проверки от 10 ноября (дополнение)

- Изолированный клон и GitHub origin подтверждены; deps 402 пакета, lint PASS, tsc PASS, deployment-env selftest PASS.
- Полная регрессия AI PROF — **602/602 PASS**.
- **Релизный gate KÖL BLOCKED**: `npm run check:release-source` останавливается на `npm audit --omit=dev --audit-level=high`. По локальному выводу аудита: Next.js (1 critical), Sharp и source-map-js (2 high). Исправления автоматически не выполнялись, `npm audit fix --force` запрещён.
- `kol_v4_pr_preflight.py` проверяет npm audit, системное разрешение записи, точный SHA/чистоту/remote, исходный профиль и GitHub Gateway/Publisher. Любой BLOCKED запрещает создание V4 PR задачи.
- Правило systemd записано как исходный артефакт, но **не применено в systemd**. Службы работают как ранее.
- Следующий рабочий путь: отдельная безопасная проверка и обновление зависимостей базовой ветки KÖL через security PR, тесты, согласование/merge владельцем; затем обновить закреплённый SHA профиля и провести V4 E2E. Не обходить npm audit и не ослаблять Required-Checks.

## Статус продолжения 10 ноября, 12:45–12:55

- PR №157: 13/13 GitHub Checks PASS, Ready for Review, **ещё не объединён в main**.
- Отдельный постоянный PR-runner создан от чистой main на SHA 0a04ba6; два других каталога KÖL не изменены.
- Исполнители Gateway, Stage01B, Publisher и проектный реестр направлены в отдельный PR-runner, не на security-ветку.
- Перед созданием **любого** нового V4-задания теперь обязательно вызывается проверка готовности. При BLOCKED очередь не создаётся; есть адресный тест запрета.
- Полная регрессия AI PROF 609/609 PASS; самопроверка Publisher PASS. Автодеплой/merge запрещены.
- V4 readiness остаётся **BLOCKED**: security fix ещё не в main, node_modules runner отсутствуют, systemd ReadWritePaths не разрешает новую папку.
- Только после принятия PR №157: проверить remote main, обновить закреплённый SHA в preflight, синхронизировать чистую main runner, запустить npm ci и npm audit, затем применить узкий systemd drop-in владельцем и перезапустить только нужные службы.
- Сразу после PASS создать новое V4 owner issue с узким scope, пройти 01A→01B→01C→один PR. Никаких повторных импортов orphaned issue №172.
