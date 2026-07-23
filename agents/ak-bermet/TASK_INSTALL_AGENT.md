# TASK AB-AGENT-001 — INSTALL AK BERMET PROJECT AGENT V2

## Goal
Install the complete V2 knowledge and governance package in AI PROF Control Center.

## Base
Repository: `/home/agent/projects/ai-prof-control-center`
Base branch: `main`
Working branch: `feature/ak-bermet-project-agent-v2`

## Source package
All files from `ak_bermet_agent_v2`.

## Target
`agents/ak-bermet/`

## Required actions

1. Verify Control Center working tree is clean.
2. Create the working branch.
3. Install:
   - README.md
   - SYSTEM_INSTRUCTIONS.md
   - SOURCE_POLICY.md
   - KNOWLEDGE_BASE.md
   - STATE.md
   - DECISIONS.md
   - OPEN_RISKS.md
   - APPROVAL_MATRIX.md
   - ROADMAP.md
4. Install validator as `scripts/validate-ak-bermet-agent.sh`.
5. Add AK BERMET to the existing project registry only after inspecting its actual format. Do not invent a new registry format when one already exists.
6. Update agent-cycle loading so Claude and Codex receive:
   - SYSTEM_INSTRUCTIONS
   - SOURCE_POLICY
   - STATE
   - current task
   - relevant decisions/risks
7. Do not pass the entire knowledge base when a smaller relevant context is sufficient.
8. Add task-state folders only if they do not conflict with the existing Control Center structure.
9. Do not modify `/home/agent/projects/ak-bermet`.
10. Do not copy secrets.
11. Run:
    - package validator;
    - `bash -n` on changed shell scripts;
    - existing Control Center tests.
12. Produce installation report with:
    - branch;
    - commit SHA;
    - changed files;
    - tests;
    - residual risks.
13. Commit, push feature branch, no merge.

## Codex audit requirements

- verify exact package completeness;
- verify no stale hard-coded current task in system instructions;
- verify approval gates;
- verify evidence levels;
- verify no secrets;
- verify existing Control Center conventions were preserved;
- verdict PASS/FAIL.
