# AI PROF Task Schema V2

Обязательные поля:

- `Task-ID:`
- `Project-Path:`
- `Base-Branch:`
- `Work-Branch:`
- `Agent-Context:`
- `Goal:`
- `Scope:`
- `Out-of-Scope:`
- `Pass-Criteria:`
- `Required-Checks:`
- `Required-Commands:`
- `Required-Environment:`
- `Owner-Approval-Required: yes|no`

Правила:
- Work-Branch начинается только с `feature/` или `fix/`.
- Этап 01A не запускает Claude, не изменяет целевой проект, не делает merge/push/deploy.
- Он только валидирует задачу, доступы и контекст, затем безопасно перемещает её в `review`.
