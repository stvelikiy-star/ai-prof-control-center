# Approval Matrix

## Autonomous inside validated self-maintenance scope
- edit explicitly scoped Telegram bridge / ChatGPT gateway implementation files
- edit explicitly scoped tests, docs, reports and CI workflows
- run only outer-runner allowlisted checks
- prepare a proposed patch for outer review

## Always owner-gated / outside this profile
- changing `orchestrator/projects.json`, `config.json`, task intake, project registry or operation profiles
- changing sandbox / Codex / control-loop security boundaries
- commits, pushes, merges, tags or release activation
- systemd restart/reload/enable/disable
- production deploy, migrations, restore, database write or destructive operation
- secrets, credentials, environment values or access-policy changes
- broadening this project's allowed scope or capabilities

Missing authority means BLOCKED / OWNER_ACTION_REQUIRED. Never infer approval from task urgency or prior production work.
