#!/usr/bin/env bash
set -Eeuo pipefail

CONTROL_CENTER="/home/agent/projects/ai-prof-control-center"

usage() {
  echo "Использование:"
  echo "  $0 <project_path> <task_file>"
  echo
  echo "Пример:"
  echo "  $0 /home/agent/projects/ak-bermet tasks/AK_BERMET_NEXT_TASK.md"
}

if [[ $# -ne 2 ]]; then
  usage
  exit 1
fi

PROJECT_PATH="$(realpath "$1")"

if [[ "$2" = /* ]]; then
  TASK_FILE="$(realpath "$2")"
else
  TASK_FILE="$(realpath "$CONTROL_CENTER/$2")"
fi

TEAM_RULES="$CONTROL_CENTER/agents/TEAM_RULES.md"
PROJECT_NAME="$(basename "$PROJECT_PATH")"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$CONTROL_CENTER/runs/${PROJECT_NAME}_${TIMESTAMP}"
CLAUDE_PROMPT="$RUN_DIR/CLAUDE_PROMPT.md"
CODEX_REPORT="$CONTROL_CENTER/reports/${PROJECT_NAME}_CODEX_${TIMESTAMP}.md"

for required in \
  "$PROJECT_PATH/.git" \
  "$TASK_FILE" \
  "$TEAM_RULES"
do
  if [[ ! -e "$required" ]]; then
    echo "Ошибка: не найдено $required"
    exit 1
  fi
done

if [[ -n "$(git -C "$PROJECT_PATH" status --porcelain)" ]]; then
  echo "Ошибка: рабочая директория проекта содержит незакоммиченные изменения."
  git -C "$PROJECT_PATH" status --short
  exit 1
fi

mkdir -p "$RUN_DIR"

cat > "$CLAUDE_PROMPT" <<PROMPT
Read these files first:

$TEAM_RULES
$TASK_FILE

Project repository:
$PROJECT_PATH

Execute the task completely in the current project branch.

Rules:
- Follow the task scope exactly.
- Do not expose secrets.
- Do not merge branches.
- Do not expand scope.
- Run only the checks required by the task.
- Commit and push only when the task explicitly requires it.
- Return a concise implementation summary.
PROMPT

echo
echo "=================================================="
echo " AI PROF AGENT CYCLE"
echo "=================================================="
echo "Проект:  $PROJECT_PATH"
echo "Задание: $TASK_FILE"
echo "Ветка:   $(git -C "$PROJECT_PATH" branch --show-current)"
echo "Run:     $RUN_DIR"
echo

echo "ЭТАП 1 — CLAUDE CODE"
echo "В проекте будет открыт Claude Code."
echo "Промт уже подготовлен:"
echo "$CLAUDE_PROMPT"
echo
echo "Скопируйте его содержимое командой:"
echo "cat \"$CLAUDE_PROMPT\""
echo

cd "$PROJECT_PATH"
claude

echo
echo "ЭТАП 2 — CODEX READ-ONLY AUDIT"
echo

codex exec --ephemeral \
  -o "$CODEX_REPORT" \
  "Read $TEAM_RULES and $TASK_FILE. Independently audit the current repository result. Inspect git status, current branch, commits, diff, task scope, security, regressions, and required checks. Do not modify files, switch branches, install packages, merge, or expose secrets. Return findings by severity and final verdict PASS, PASS WITH FIXES, or FAIL."

echo
echo "=================================================="
echo " ЦИКЛ ЗАВЕРШЁН"
echo "=================================================="
echo "Отчёт Codex:"
echo "$CODEX_REPORT"
echo
echo "Покажите отчёт:"
echo "cat \"$CODEX_REPORT\""
