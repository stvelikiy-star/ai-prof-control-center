#!/usr/bin/env bash
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "ROOT_REQUIRED"; exit 10; }
ROOT=/home/agent/projects/ai-prof-control-center
STATE=/home/agent/.local/state/ai-prof-control-center
SERVICE=ai-prof-control-center.service
CONF_DIR=/etc/systemd/system/ai-prof-control-center.service.d
CONF="$CONF_DIR/zzz-kol-writable-only-20261109.conf"
KOL=/home/agent/Загрузки/kol-travel-platform
[ -d "$KOL/.git" ] && [ ! -L "$KOL" ] || { echo "KOL_CHECKOUT_INVALID"; exit 11; }
[ ! -e "$CONF" ] || { echo "OVERRIDE_ALREADY_PRESENT; refuse overwrite"; exit 12; }
[ -d "$STATE/queue/active" ] || { echo "QUEUE_MISSING"; exit 13; }
for q in active review pending_codex; do
  if find "$STATE/queue/$q" -maxdepth 1 -type f -name '*.md' -print -quit | grep -q .; then
    echo "ACTIVE_TASK_REFUSE_RESTART:$q"; exit 14
  fi
done
/usr/bin/python3 "$ROOT/orchestrator/control_loop_service_night.py" --self-test >/dev/null
# Fix exact duplicate rules, preserving one under /etc/logrotate.d.
bash "$ROOT/repair-system-logrotate-20261108.sh"
install -d -m 0755 -o root -g root "$CONF_DIR"
TMP="$(mktemp "$CONF_DIR/.kol-conf.XXXXXXXX")"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<'CONF_TEXT'
[Service]
# Scope new KOL tasks to this validated checkout only. MARINA SMART excluded.
ReadWritePaths=/home/agent/Загрузки/kol-travel-platform
CONF_TEXT
chmod 0644 "$TMP"
chown root:root "$TMP"
mv "$TMP" "$CONF"
systemctl daemon-reload
if ! systemctl show "$SERVICE" -p ReadWritePaths --value | grep -Fq "$KOL"; then
  rm -f "$CONF"; systemctl daemon-reload
  echo "KOL_WRITE_ALLOWLIST_NOT_ACTIVE; ROLLED_BACK"; exit 15
fi
if ! systemctl restart "$SERVICE"; then
  rm -f "$CONF"; systemctl daemon-reload
  systemctl restart "$SERVICE" || true
  echo "CONTROL_CENTER_RESTART_FAILED; CONFIG_ROLLED_BACK"; exit 16
fi
sleep 3
if ! systemctl is-active --quiet "$SERVICE"; then
  rm -f "$CONF"; systemctl daemon-reload
  systemctl restart "$SERVICE" || true
  echo "CONTROL_CENTER_NOT_ACTIVE; CONFIG_ROLLED_BACK"; exit 17
fi
PID="$(systemctl show "$SERVICE" -p MainPID --value)"
[ "$PID" -gt 1 ] || { echo "CONTROL_CENTER_PID_INVALID"; exit 18; }
BRIDGE=ai-prof-telegram-bridge.service
systemctl restart "$BRIDGE" || { echo "TELEGRAM_BRIDGE_RESTART_FAILED"; exit 19; }
sleep 2
BRIDGE_PID="$(systemctl show "$BRIDGE" -p MainPID --value)"
if ! systemctl is-active --quiet "$BRIDGE" || [ "$BRIDGE_PID" -le 1 ] ||
   ! grep -aFq 'telegram_bridge_v5.py' "/proc/$BRIDGE_PID/cmdline"; then
  echo "TELEGRAM_V5_NOT_CONFIRMED"; exit 20
fi
echo "AI_PROF_HOST_REPAIR_PASS CONTROL_PID=$PID BRIDGE_PID=$BRIDGE_PID"
systemctl show "$SERVICE" -p ActiveState -p NRestarts -p ReadWritePaths
systemctl show "$BRIDGE" -p ActiveState -p NRestarts -p MainPID
systemctl show logrotate.service -p ActiveState -p Result
