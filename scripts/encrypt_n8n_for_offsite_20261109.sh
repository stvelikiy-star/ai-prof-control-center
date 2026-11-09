#!/usr/bin/env bash
# Prepare the n8n database/config/volume backup for independent off-site transport.
set -Eeuo pipefail
umask 077
ROOT=/home/agent/ai-prof-backups
OUT=$ROOT/n8n-offsite-encrypted
KEY=/home/agent/.config/ai-prof-control-center/recovery-key.gpg-passphrase
SRC=""
TMP=""
VERIFY=""
cleanup() { [ -z "$VERIFY" ] || rm -rf -- "$VERIFY"; [ -z "$TMP" ] || rm -f -- "$TMP"; }
trap cleanup EXIT
[ -f "$KEY" ] && [ ! -L "$KEY" ] && [ "$(stat -c '%a' "$KEY")" = 600 ] ||
 { echo "RECOVERY_KEY_NOT_READY"; exit 10; }
install -d -m 0700 "$OUT"
exec 9>"$OUT/.n8n-offsite-backup.lock"
flock -n 9 || { echo "N8N_OFFSITE_ENCRYPTION_BUSY"; exit 11; }
# Only consider complete dated backup folders; never traverse other projects.
while IFS= read -r dir; do
 if [ -s "$dir/SHA256SUMS" ] && [ -s "$dir/database/n8n.dump" ] &&
    [ -s "$dir/volumes/n8n-data.tar.gz" ] &&
    [ -s "$dir/config/ai-prof-config.tar.gz" ]; then
   SRC="$dir"; break
 fi
done < <(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
  -regex '.*/20[0-9]{2}-[0-9]{2}-[0-9]{2}_[0-9]{6}' | sort -r)
[ -n "$SRC" ] || { echo "COMPLETE_N8N_BACKUP_NOT_FOUND"; exit 12; }
(
 cd "$SRC"
 sha256sum -c SHA256SUMS >/dev/null
)
NAME="n8n-ai-prof-$(basename "$SRC").tar.gz.gpg"
FINAL="$OUT/$NAME"
if [ -s "$FINAL" ] && [ -s "$FINAL.sha256" ] &&
   ( cd "$OUT" && sha256sum -c "$NAME.sha256" >/dev/null ); then
 echo "N8N_ENCRYPTED_OFFSITE_ALREADY_VERIFIED archive=$NAME"
 exit 0
fi
[ ! -e "$FINAL" ] || { echo "EXISTING_UNVERIFIED_ARCHIVE"; exit 13; }
TMP="$(mktemp "$OUT/.encrypted-n8n.XXXXXXXX")"
(
 cd "$SRC"
 tar -czf - SHA256SUMS database/n8n.dump volumes/n8n-data.tar.gz config/ai-prof-config.tar.gz
) | gpg --batch --yes --no-tty --pinentry-mode loopback \
 --passphrase-file "$KEY" --symmetric --cipher-algo AES256 --output "$TMP"
[ -s "$TMP" ] || { echo "EMPTY_ENCRYPTED_ARCHIVE"; exit 14; }
# Prove decryption and re-check all original SHA256 hashes inside a RAM disk.
VERIFY="$(mktemp -d /dev/shm/ai-prof-offsite-n8n-XXXXXXXX)"
gpg --batch --quiet --no-tty --pinentry-mode loopback \
 --passphrase-file "$KEY" --decrypt "$TMP" |
 tar -xzf - -C "$VERIFY" --no-same-owner --no-same-permissions
(
 cd "$VERIFY"
 sha256sum -c SHA256SUMS >/dev/null
)
chmod 0600 "$TMP"
mv "$TMP" "$FINAL"
TMP=""
( cd "$OUT" && sha256sum "$NAME" ) > "$FINAL.sha256"
chmod 0600 "$FINAL.sha256"
echo "N8N_OFFSITE_ENCRYPTED_RECOVERY_PASS archive=$NAME"
