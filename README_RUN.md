# Запуск AI PROF Control Center

```bash
cd /home/agent/projects/ai-prof-control-center
./scripts/submit-task projects
./scripts/submit-task create --project ai-prof-pilot \
  --title "Task title" --instructions "One-line implementation instruction" \
  --work-branch feature/task-name --scope README.md
./scripts/control-center --once
```

Управление постоянным циклом:

```bash
./scripts/control-center --daemon
./scripts/control-center --status
./scripts/control-center --pause
./scripts/control-center --resume
./scripts/control-center --stop
```

Установка user-unit без запуска:

```bash
./scripts/install-user-service.sh
```
