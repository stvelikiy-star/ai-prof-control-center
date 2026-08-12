# ChatGPT Control Gateway V1

## Purpose

The gateway lets the owner discuss a goal with GPT in ChatGPT mobile/web and then submit one structured task into the existing AI PROF queue without copying a Telegram command or opening SSH.

Flow:

`ChatGPT/GPT -> private GitHub issue -> github_task_gateway.py -> submit_task.py -> existing AI PROF queue -> agents/Codex`

Task state is written back to the same private GitHub issue. Telegram remains a parallel control/notification channel.

## Fixed V1 trust boundary

The V1 importer is intentionally hard-bound in code to:

- repository: `stvelikiy-star/ai-prof-control-center`
- issue author: `stvelikiy-star`

Environment variables cannot broaden these trust anchors.

The gateway ignores ordinary issues. A task issue must have both:

1. title prefix: `[AI-PROF-TASK] `
2. body first line: `AI-PROF-TASK-V1`

Pull requests are never accepted as tasks.

## V1 authority

Allowed task actions are code-only:

- `code-edit`
- `tests`
- `docs`

Every contract must explicitly preserve these forbidden actions:

- `commit`
- `push`
- `merge`
- `deployment`
- `secrets`
- `destructive-operations`

Production approval is **not** implemented through GitHub task issues. A future bounded owner-approval runner is a separate security boundary.

## Exact issue contract

Example title:

`[AI-PROF-TASK] Improve Telegram blocker diagnostics`

Example body:

```text
AI-PROF-TASK-V1
{"version":1,"project":"ai-prof-control-center","title":"Improve Telegram blocker diagnostics","objective":"Improve the explicitly scoped blocker diagnostic output without changing authorization or production policy.","priority":"normal","scope":["orchestrator/telegram_bridge_v2.py","tests/test_telegram_bridge_v2.py"],"allowed_actions":["code-edit","tests"],"forbidden_actions":["commit","push","merge","deployment","secrets","destructive-operations"],"owner_approval_gates":["live service activation requires separate owner approval"],"acceptance_criteria":["existing Telegram commands remain compatible","owner-only authorization remains enforced","full tests pass"]}
```

The JSON object must contain exactly these keys:

- `version`
- `project`
- `title`
- `objective`
- `priority`
- `scope`
- `allowed_actions`
- `forbidden_actions`
- `owner_approval_gates`
- `acceptance_criteria`

No extra keys are accepted.

## Validation and execution

The gateway does not run issue text as a command. It converts the validated contract to an argv call of the existing `submit_task.py` intake. That existing intake remains authoritative for:

- known project validation
- allowed base branch
- generated work branch
- allowed scope
- project safety flags
- queue file creation

The work branch is generated from the issue number as `feature/chatgpt-issue-<N>`; the issue cannot choose arbitrary branch text.

## Secret handling

Do not put secrets in a GitHub task issue.

Before enqueue, each structured string value is checked with the existing AI PROF secret redactor. Token/password/credential/database-URL-like material causes the contract to be rejected rather than copied into the local task queue.

Any status or rejection text posted back to GitHub is redacted again.

## Exactly-once behavior

Normal deduplication is persisted in a private mode-0600 gateway state file.

For crash recovery, every imported task contains this marker:

`Source: authorized private GitHub task issue #<N>.`

Before creating a new task the gateway scans existing AI PROF queue artifacts for that marker. If one task already exists, the mapping is recovered and no duplicate is created. More than one matching task is treated as a safety incident and blocks instead of guessing.

## Service hardening

The staged service is `systemd/ai-prof-github-task-gateway.service`.

It runs with:

- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=strict`
- `ProtectHome=read-only`
- read-only access to the existing GitHub CLI config
- write access only to AI PROF state
- `UMask=0077`
- `GH_PROMPT_DISABLED=1`

No token, password or secret is embedded in the unit.

## Activation boundary

Merging the gateway into GitHub `main` does not activate it on Ubuntu. Live activation requires the staged Control Center update and explicit service enable/start step after host checks.
