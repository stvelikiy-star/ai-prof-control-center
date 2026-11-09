#!/usr/bin/env bash
set -Eeuo pipefail
BACKUP="/home/agent/ai-prof-backups/2026-11-09_030042/database/n8n.dump"
NAME="ai-prof-restore-verify-20261109"
[ -f "$BACKUP" ] && [ ! -L "$BACKUP" ] || { echo "BACKUP_MISSING"; exit 10; }
[ "$(stat -c %s "$BACKUP")" -gt 10240 ] || { echo "BACKUP_TOO_SMALL"; exit 11; }
if docker container inspect "$NAME" >/dev/null 2>&1; then
 echo "RESTORE_CONTAINER_ALREADY_EXISTS"; exit 12
fi
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --detach --rm --name "$NAME" --network none \
 --memory 384m --memory-swap 384m --cpus 0.5 --pids-limit 100 \
 --tmpfs /var/lib/postgresql/data:rw,size=256m,mode=1777 \
 -e POSTGRES_HOST_AUTH_METHOD=trust -e PGDATA=/var/lib/postgresql/data/pgdata \
 postgres:17-alpine -c shared_buffers=16MB -c max_connections=10 >/dev/null
READY=0
for _ in $(seq 1 45); do
 if docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; then READY=1; break; fi
 sleep 1
done
if [ "$READY" -ne 1 ]; then
 echo "ISOLATED_POSTGRES_NOT_READY"
 docker inspect "$NAME" --format 'container_state={{.State.Status}} exit_code={{.State.ExitCode}}' 2>/dev/null || true
 docker logs --tail 14 "$NAME" 2>&1 | sed -E 's/(password|secret|token)([=: ])[^ ]+/\\1=[REDACTED]/Ig' | cut -c1-180 || true
 exit 13
fi
docker cp "$BACKUP" "$NAME:/tmp/n8n.dump" >/dev/null
docker exec "$NAME" createdb -U postgres ai_prof_restore_evidence
# Do not print row values or secrets from the n8n database.
if ! docker exec "$NAME" pg_restore -U postgres -d ai_prof_restore_evidence \
 --no-owner --no-acl --exit-on-error /tmp/n8n.dump >/tmp/ai-prof-pg-restore-20261109.log 2>&1; then
 echo "ISOLATED_RESTORE_FAIL"; tail -n 8 /tmp/ai-prof-pg-restore-20261109.log | cut -c1-200; exit 14
fi
tables="$(docker exec "$NAME" psql -U postgres -d ai_prof_restore_evidence -At -c "select count(*) from information_schema.tables where table_schema='public';")"
workflows="$(docker exec "$NAME" psql -U postgres -d ai_prof_restore_evidence -At -c 'select count(*) from public.workflow_entity;' 2>/dev/null || echo query_unavailable)"
printf 'ISOLATED_RESTORE_PASS public_tables=%s workflow_count=%s network=none persistent_volumes=none\n' "$tables" "$workflows"
[ "$tables" -gt 5 ] || { echo "INSUFFICIENT_RESTORED_TABLES"; exit 15; }
