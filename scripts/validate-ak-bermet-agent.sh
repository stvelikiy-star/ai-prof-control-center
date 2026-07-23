#!/usr/bin/env bash
set -euo pipefail

CONTROL_CENTER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$CONTROL_CENTER/agents/ak-bermet}"

exec "$TARGET/validate_agent_package.sh" "$TARGET"
