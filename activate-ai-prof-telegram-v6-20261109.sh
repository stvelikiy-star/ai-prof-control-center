#!/usr/bin/env bash
set -Eeuo pipefail
SERVICE=ai-prof-telegram-bridge.service
ROOT=/home/agent/projects/ai-prof-control-center
STATE=/home/agent/.local/state/ai-prof-control-center
LEDGER=$STATE/telegram-task-notified-v6.json
[ "$(id -u)" -eq 0 ] || { echo "ROOT_REQUIRED"; exit 10; }
[ -f "$ROOT/orchestrator/telegram_bridge_v5.py" ] &&
[ -f "$ROOT/orchestrator/telegram_bridge_v6.py" ] || { echo "BRIDGE_SOURCE_MISSING"; exit 11; }
BEFORE="$(systemctl show "$SERVICE" -p MainPID --value)"
[ "$BEFORE" -gt 1 ] && systemctl is-active --quiet "$SERVICE" ||
 { echo "BRIDGE_NOT_ACTIVE_BEFORE"; exit 12; }
grep -Fq "telegram_bridge_v5.py" "/proc/$BEFORE/cmdline" ||
 { echo "UNEXPECTED_SERVICE_ENTRYPOINT"; exit 13; }
# Preflight as the service user. Never print Telegram secrets.
runuser -u agent -- /usr/bin/python3 -m py_compile \
 "$ROOT/orchestrator/telegram_bridge_v5.py" \
 "$ROOT/orchestrator/telegram_bridge_v6.py"
runuser -u agent -- /usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, '/home/agent/projects/ai-prof-control-center/orchestrator')
import telegram_bridge_v5 as v5
import telegram_bridge_v6 as v6
ledger = v6.read_state(v6.STATE_PATH)
live = v6.enabled_task_snapshot()
assert ledger is not None
assert len(live) <= v6.MAX_NOTIFIED_TASKS
assert not any(status[1]=='resort-os' for status in live.values())
assert isinstance(v5.owner_approvals_report(), str)
print('TELEGRAM_V6_PREFLIGHT_PASS')
PY
# No unit-file changes: the installed V5 entrypoint now loads V6 internally.
systemctl restart "$SERVICE" ||
 { echo "TELEGRAM_V6_RESTART_FAILED"; exit 20; }
sleep 3
AFTER="$(systemctl show "$SERVICE" -p MainPID --value)"
if ! systemctl is-active --quiet "$SERVICE" ||
   [ "$AFTER" -le 1 ] || [ "$AFTER" = "$BEFORE" ] ||
   ! grep -aFq "telegram_bridge_v5.py" "/proc/$AFTER/cmdline"; then
 echo "TELEGRAM_V6_POST_RESTART_GATE_FAILED"
 exit 21
fi
# Recheck the new process persists and its delivery ledger stays readable.
sleep 3
systemctl is-active --quiet "$SERVICE" ||
 { echo "TELEGRAM_V6_UNHEALTHY_AFTER_RESTART"; exit 22; }
runuser -u agent -- /usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, '/home/agent/projects/ai-prof-control-center/orchestrator')
import telegram_bridge_v6 as v6
assert v6.read_state(v6.STATE_PATH) is not None
print('TELEGRAM_V6_LEDGER_PASS')
PY
echo "TELEGRAM_V6_ACTIVATION_PASS old_pid=$BEFORE new_pid=$AFTER"
systemctl show "$SERVICE" -p ActiveState -p MainPID -p NRestarts
