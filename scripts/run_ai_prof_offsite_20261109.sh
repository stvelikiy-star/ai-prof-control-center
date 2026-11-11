#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/home/agent/projects/ai-prof-control-center
CONFIG=/home/agent/.config/ai-prof-control-center/offsite-sftp.json
[ -s "$CONFIG" ] || { echo "ВНЕШНИЙ БЭКАП НЕ НАСТРОЕН: нет SFTP-сервера"; exit 12; }
# Re-verify local state, then create an encrypted n8n package and upload both.
python3 "$ROOT/orchestrator/control_plane_backup_health.py" | grep -q '^PASS|'
bash "$ROOT/scripts/encrypt_n8n_for_offsite_20261109.sh"
python3 "$ROOT/scripts/offsite_control_plane_sftp.py"
