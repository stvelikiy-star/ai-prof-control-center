# AI PROF Control Center

Центр управления ИИ-командой AI PROF.

## Роли

- ChatGPT — руководитель, бизнес-аналитик и архитектор.
- Claude Code — основной разработчик.
- Codex — проверка, тестирование, диагностика и исправления.
- Владелец — утверждает бизнес-решения и доступ к production.

## Рабочий цикл

1. Получить контекст проекта.
2. Создать точное задание.
3. Выполнить работу в отдельной Git-ветке.
4. Запустить необходимые проверки.
5. Провести независимый аудит.
6. Сформировать отчёт.
7. Передать результат на утверждение.
8. Только после проверки выполнить merge.

## Production intake milestone

Единственный зарегистрированный intake-проект:
`ai-prof-pilot` (`/home/agent/projects/ai-prof-pilot-sandbox`, ветка `develop`).

Создание задачи одной локальной командой:

```bash
./scripts/submit-task create \
  --project ai-prof-pilot \
  --title "Update pilot documentation" \
  --instructions "Clarify the local verification workflow" \
  --work-branch feature/clarify-verification \
  --scope README.md
```

Проверить очередь и supervisor:

```bash
./scripts/submit-task list
./scripts/control-center --status
./scripts/control-center --once
```

Claude работает только в Bubblewrap и применяет изменения только из
`Scope-Files`. Codex запускается независимо в `read-only` sandbox.
Commit, push, merge и deployment отключены.

## Runtime state

Очереди задач, PID/locks, heartbeat и журналы являются runtime-данными и
хранятся вне Git worktree в
`/home/agent/.local/state/ai-prof-control-center`. Путь можно переопределить
переменной `AI_PROF_STATE_DIR` или аргументом `--state-root`. `--root`
по-прежнему указывает на исходный код и реестр проектов.
