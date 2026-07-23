Task-ID: SELF-TEST-002
Project-Path: /home/agent/projects/ak-bermet
Base-Branch: develop
Work-Branch: feature/orchestrator-self-test
Agent-Context: agents/ak-bermet
Goal: Validate secure queue handling without changing the target project.
Scope: Stage 01A validation only.
Out-of-Scope: Claude execution, code changes, Git branch creation, commit, merge, push, database, deployment.
Pass-Criteria: Secure validation moves the task from pending to review.
Required-Checks: Context content loaded, clean Git, command access, collision-safe move, redacted logs.
Required-Commands: git, python3
Required-Environment: none
Owner-Approval-Required: no
