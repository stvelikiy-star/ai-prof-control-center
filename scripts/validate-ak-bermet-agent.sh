#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

required=(
  README.md
  SYSTEM_INSTRUCTIONS.md
  SOURCE_POLICY.md
  KNOWLEDGE_BASE.md
  STATE.md
  DECISIONS.md
  OPEN_RISKS.md
  APPROVAL_MATRIX.md
  ROADMAP.md
  TASK_INSTALL_AGENT.md
  TASK_PHASE_01_VERIFY_RUNTIME.md
)

for file in "${required[@]}"; do
  path="$ROOT/$file"
  [[ -f "$path" ]] || { echo "MISSING: $file" >&2; exit 1; }
  [[ -s "$path" ]] || { echo "EMPTY: $file" >&2; exit 1; }
done

for pattern in \
  'MANAGER_ACCESS_PIN=' \
  'SUPABASE_SERVICE_ROLE_KEY=[^[:space:]]+' \
  'sk-[A-Za-z0-9_-]{20,}' \
  'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY'
do
  if grep -RIE --exclude='validate_agent_package.sh' "$pattern" "$ROOT"; then
    echo "POTENTIAL SECRET FOUND: $pattern" >&2
    exit 1
  fi
done

grep -q 'production-проект' "$ROOT/SYSTEM_INSTRUCTIONS.md"
grep -q 'STAGING_PASS' "$ROOT/SYSTEM_INSTRUCTIONS.md"
grep -q '60 минут' "$ROOT/DECISIONS.md"
grep -q '17 тестовых пользователей' "$ROOT/ROADMAP.md"
grep -q 'BLOCKED_MISSING_ACCESS' "$ROOT/TASK_PHASE_01_VERIFY_RUNTIME.md"

echo "AK BERMET agent package: PASS"
