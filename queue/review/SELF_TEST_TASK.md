Task-ID: SELF-TEST-001
Project-Path: /home/agent/projects/ak-bermet
Base-Branch: develop
Work-Branch: test/orchestrator-self-test
Agent-Context: agents/ak-bermet
Goal: Validate orchestrator task parsing and queue movement without modifying the project.
Scope: Dry-run only.
Out-of-Scope: Code changes, Git branches, commits, merge, database actions, deployment.
Pass-Criteria: Task validates and moves from pending to review.
Required-Checks: Orchestrator dry-run returns DRY_RUN_PASS.
Owner-Approval-Required: no
