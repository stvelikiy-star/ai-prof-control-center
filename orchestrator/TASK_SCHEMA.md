# AI PROF Task Schema

Каждая задача в `queue/pending/` должна быть Markdown-файлом и содержать:

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
- `Owner-Approval-Required: yes|no`

Оркестратор не выполняет merge и production deploy.
