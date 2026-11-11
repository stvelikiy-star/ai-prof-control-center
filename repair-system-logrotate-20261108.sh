#!/usr/bin/env bash
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "ROOT_REQUIRED"; exit 10; }
ORIGINAL=/etc/logrotate.d/cloud-init-base
KEEP=/etc/logrotate.d/cloud-init
BACKUP_DIR=/root/ai-prof-system-repair-20261108
[ -f "$ORIGINAL" ] && [ -f "$KEEP" ] || { echo "CLOUD_INIT_CONFIG_MISSING"; exit 11; }
cmp -s "$ORIGINAL" "$KEEP" || { echo "RULES_DIFFER_REFUSE_CHANGE"; exit 12; }
mkdir -p "$BACKUP_DIR"
[ ! -e "$BACKUP_DIR/cloud-init-base" ] || { echo "BACKUP_ALREADY_EXISTS"; exit 13; }
mv "$ORIGINAL" "$BACKUP_DIR/cloud-init-base"
if ! /usr/sbin/logrotate -d /etc/logrotate.conf >"$BACKUP_DIR/logrotate-dryrun.log" 2>&1; then
  mv "$BACKUP_DIR/cloud-init-base" "$ORIGINAL"
  echo "LOGROTATE_DRYRUN_FAILED_ROLLED_BACK"
  exit 14
fi
systemctl reset-failed logrotate.service || true
if systemctl start logrotate.service; then
  echo "LOGROTATE_SYSTEM_PASS"
else
  echo "LOGROTATE_SYSTEM_RETRY_REQUIRED; original rule preserved in $BACKUP_DIR"
  exit 15
fi
