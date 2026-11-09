#!/usr/bin/env bash
# Encrypted, independently restorable AI PROF control-plane snapshot.
set -Eeuo pipefail
umask 077
STATE=/home/agent/.local/state/ai-prof-control-center
BACKUPS=/home/agent/ai-prof-backups/control-plane-encrypted
KEY_DIR=/home/agent/.config/ai-prof-control-center
KEY=$KEY_DIR/recovery-key.gpg-passphrase
RESTORE_TMP=""
OUT_TMP=""
cleanup() {
  if [ -n "$RESTORE_TMP" ]; then rm -rf -- "$RESTORE_TMP"; fi
  if [ -n "$OUT_TMP" ]; then rm -f -- "$OUT_TMP"; fi
}
trap cleanup EXIT
[ -d "$STATE/queue" ] && [ -d "$STATE/logs/orchestrator" ] ||
 { echo "CONTROL_PLANE_STATE_MISSING"; exit 10; }
[ ! -L "$STATE" ] && [ ! -L "$STATE/queue" ] || { echo "UNSAFE_STATE_PATH"; exit 11; }
install -d -m 0700 "$BACKUPS" "$KEY_DIR"
# Shared by manual and systemd runs. Never permit overlapping snapshots.
LOCK="$BACKUPS/.control-plane-backup.lock"
exec 9>"$LOCK"
flock -n 9 || { echo "CONTROL_PLANE_BACKUP_ALREADY_RUNNING"; exit 19; }
if [ ! -f "$KEY" ]; then
  (umask 077; head -c 48 /dev/urandom | base64 -w0 > "$KEY")
fi
[ ! -L "$KEY" ] && [ "$(stat -c '%a' "$KEY")" = 600 ] ||
 { echo "UNSAFE_RECOVERY_KEY"; exit 12; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL="$BACKUPS/control-plane-$STAMP.tar.gz.gpg"
[ ! -e "$FINAL" ] || { echo "SNAPSHOT_EXISTS"; exit 13; }
OUT_TMP="$(mktemp "$BACKUPS/.control-plane.XXXXXXXX")"
# Never archive transient locks/pids, worktree contents or task execution secrets.
# Queue, stage audit logs and delivery offsets are necessary to reconstruct task state.
tar -czf - -C "$STATE" \
  --exclude='logs/orchestrator/control-loop.log' \
  --exclude='logs/orchestrator/control-loop.log.1.gz' \
  queue logs/orchestrator campaigns plans evidence reports \
  telegram-task-notified-v6.json telegram-terminal-notified-v3.json \
  telegram-update-offset github-task-gateway.json \
  | gpg --batch --yes --no-tty --pinentry-mode loopback \
     --passphrase-file "$KEY" --symmetric --cipher-algo AES256 \
     --output "$OUT_TMP"
[ -s "$OUT_TMP" ] || { echo "ENCRYPTED_SNAPSHOT_EMPTY"; exit 14; }
# Test actual decrypt + archive extraction on RAM disk; never alter live queues.
RESTORE_TMP="$(mktemp -d /dev/shm/ai-prof-recovery-XXXXXXXX)"
gpg --batch --quiet --no-tty --pinentry-mode loopback --passphrase-file "$KEY" \
  --decrypt "$OUT_TMP" | tar -xzf - -C "$RESTORE_TMP" \
  --no-same-owner --no-same-permissions
[ -d "$RESTORE_TMP/queue/approved" ] || { echo "RESTORED_QUEUE_MISSING"; exit 15; }
[ -f "$RESTORE_TMP/github-task-gateway.json" ] || { echo "RESTORED_GATEWAY_STATE_MISSING"; exit 16; }
[ -f "$RESTORE_TMP/telegram-task-notified-v6.json" ] || { echo "RESTORED_TELEGRAM_LEDGER_MISSING"; exit 17; }
SOURCE_TASKS="$(find "$STATE/queue" -type f -name '*.md' | wc -l)"
RESTORED_TASKS="$(find "$RESTORE_TMP/queue" -type f -name '*.md' | wc -l)"
[ "$SOURCE_TASKS" = "$RESTORED_TASKS" ] || { echo "QUEUE_COUNT_MISMATCH"; exit 18; }
# The live queue may change while tar is created. Count alone is not enough:
# compare every name and byte; fail closed rather than accept a torn snapshot.
if ! diff --recursive --brief --no-dereference -- "$STATE/queue" "$RESTORE_TMP/queue" >/dev/null; then
  echo "QUEUE_CONTENT_MISMATCH"; exit 20
fi
chmod 0600 "$OUT_TMP"
mv -- "$OUT_TMP" "$FINAL"
OUT_TMP=""
(cd "$BACKUPS" && sha256sum "$(basename "$FINAL")") > "$FINAL.sha256"
chmod 0600 "$FINAL.sha256"
# Only remove old snapshots in this dedicated subdirectory after a verified new copy.
find "$BACKUPS" -maxdepth 1 -type f -name 'control-plane-*.tar.gz.gpg*' -mtime +14 -delete
echo "CONTROL_PLANE_BACKUP_RESTORE_PASS tasks=$RESTORED_TASKS encrypted_archive=$(basename "$FINAL") local_key_only=YES"
