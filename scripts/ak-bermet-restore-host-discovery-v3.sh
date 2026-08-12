#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_ROOT="/home/agent/ai-prof-backups/ak-bermet"
DOMAIN="akbermet.kg"
IMAGE="postgres:17-alpine"
CONTAINER="ak-bermet-restore-smoke-$$"
LOG="$(mktemp)"
HTML="$(mktemp)"
HEADERS="$(mktemp)"
RESTORE_RC=99

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -f "$LOG" "$HTML" "$HEADERS" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "=================================================="
echo " AK BERMET — RESTORE + HOST DISCOVERY V3"
echo "=================================================="

echo
echo "=== BACKUP DISCOVERY ==="
BACKUP=""
while IFS= read -r d; do
  if [[ -s "$d/roles.sql" && -s "$d/schema.sql" && -s "$d/data.sql" ]]; then
    BACKUP="$d"
    break
  fi
done < <(
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null |
    sort -rn |
    cut -d' ' -f2-
)

if [[ -z "$BACKUP" ]]; then
  echo "RESTORE_SMOKE=BLOCKED_NO_VALID_BACKUP"
  RESTORE_RC=90
elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "BACKUP=$BACKUP"
  echo "RESTORE_SMOKE=BLOCKED_POSTGRES_IMAGE_MISSING"
  RESTORE_RC=91
else
  echo "BACKUP=$BACKUP"
  ROLES_HASH="$(sha256sum "$BACKUP/roles.sql" | awk '{print $1}')"
  SCHEMA_HASH="$(sha256sum "$BACKUP/schema.sql" | awk '{print $1}')"
  DATA_HASH="$(sha256sum "$BACKUP/data.sql" | awk '{print $1}')"
  echo "BACKUP_ROLES_SHA256=$ROLES_HASH"
  echo "BACKUP_SCHEMA_SHA256=$SCHEMA_HASH"
  echo "BACKUP_DATA_SHA256=$DATA_HASH"

  echo
  echo "=== ISOLATED RESTORE SMOKE ==="
  docker run -d --rm \
    --name "$CONTAINER" \
    --network none \
    -e POSTGRES_PASSWORD=restore_smoke_local_only \
    -e POSTGRES_DB=restore_smoke \
    "$IMAGE" >/dev/null

  READY=0
  for _ in $(seq 1 45); do
    if docker exec -u postgres "$CONTAINER" pg_isready -q -d restore_smoke; then
      READY=1
      break
    fi
    sleep 1
  done

  if [[ "$READY" -ne 1 ]]; then
    echo "RESTORE_SMOKE=FAIL_POSTGRES_NOT_READY"
    RESTORE_RC=92
  else
    docker exec -u 0 "$CONTAINER" mkdir -p /restore-input
    docker cp "$BACKUP/roles.sql" "$CONTAINER:/restore-input/roles.sql"
    docker cp "$BACKUP/schema.sql" "$CONTAINER:/restore-input/schema.sql"
    docker cp "$BACKUP/data.sql" "$CONTAINER:/restore-input/data.sql"
    docker exec -u 0 "$CONTAINER" chown -R postgres:postgres /restore-input
    docker exec -u 0 "$CONTAINER" chmod 0700 /restore-input
    docker exec -u 0 "$CONTAINER" chmod 0600 /restore-input/roles.sql /restore-input/schema.sql /restore-input/data.sql

    CONTAINER_ROLES_HASH="$(docker exec -u postgres "$CONTAINER" sha256sum /restore-input/roles.sql | awk '{print $1}')"
    CONTAINER_SCHEMA_HASH="$(docker exec -u postgres "$CONTAINER" sha256sum /restore-input/schema.sql | awk '{print $1}')"
    CONTAINER_DATA_HASH="$(docker exec -u postgres "$CONTAINER" sha256sum /restore-input/data.sql | awk '{print $1}')"

    if [[ "$CONTAINER_ROLES_HASH" != "$ROLES_HASH" || "$CONTAINER_SCHEMA_HASH" != "$SCHEMA_HASH" || "$CONTAINER_DATA_HASH" != "$DATA_HASH" ]]; then
      echo "RESTORE_SMOKE=FAIL_COPY_HASH_MISMATCH"
      RESTORE_RC=93
    else
      set +e
      docker exec -u postgres "$CONTAINER" \
        psql --variable ON_ERROR_STOP=1 --dbname restore_smoke --file /restore-input/roles.sql \
        >"$LOG" 2>&1
      RC1=$?

      if [[ "$RC1" -eq 0 ]]; then
        docker exec -u postgres "$CONTAINER" \
          psql --single-transaction --variable ON_ERROR_STOP=1 \
          --dbname restore_smoke \
          --file /restore-input/schema.sql \
          --command 'SET session_replication_role = replica' \
          --file /restore-input/data.sql \
          >>"$LOG" 2>&1
        RESTORE_RC=$?
      else
        RESTORE_RC=$RC1
      fi
      set -e

      if [[ "$RESTORE_RC" -eq 0 ]]; then
        TABLE_COUNT="$(docker exec -u postgres "$CONTAINER" psql -Atq -d restore_smoke -c "select count(*) from pg_tables where schemaname='public';")"
        CORE_TABLES="$(docker exec -u postgres "$CONTAINER" psql -Atq -d restore_smoke -c "select coalesce(string_agg(tablename, ',' order by tablename),'') from pg_tables where schemaname='public' and tablename in ('rooms','leads','bookings','cleaning_tasks','maintenance_requests');")"
        MIGRATION_ROWS="$(docker exec -u postgres "$CONTAINER" psql -Atq -d restore_smoke -c "select count(*) from supabase_migrations.schema_migrations;" 2>/dev/null || true)"
        echo "RESTORE_SMOKE=PASS"
        echo "RESTORED_PUBLIC_TABLES=$TABLE_COUNT"
        echo "RESTORED_CORE_TABLES=$CORE_TABLES"
        echo "RESTORED_MIGRATION_ROWS=${MIGRATION_ROWS:-UNKNOWN}"
      else
        echo "RESTORE_SMOKE=FAIL"
        echo "RESTORE_RC=$RESTORE_RC"
        echo "RESTORE_ERROR_TAIL_BEGIN"
        tail -40 "$LOG" || true
        echo "RESTORE_ERROR_TAIL_END"
      fi
    fi
  fi
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

echo
echo "=== PUBLIC HOST DISCOVERY ==="
if command -v dig >/dev/null 2>&1; then
  echo "DNS_A=$(dig +short A "$DOMAIN" | paste -sd, -)"
  echo "DNS_AAAA=$(dig +short AAAA "$DOMAIN" | paste -sd, -)"
  echo "DNS_CNAME=$(dig +short CNAME "$DOMAIN" | paste -sd, -)"
  echo "DNS_NS=$(dig +short NS "$DOMAIN" | paste -sd, -)"
else
  echo "DNS_A=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd, -)"
  echo "DNS_TOOL=GETENT"
fi

curl -sS -L --max-time 30 \
  -D "$HEADERS" \
  -o "$HTML" \
  -w 'HTTP_CODE=%{http_code}\nHTTP_REMOTE_IP=%{remote_ip}\nHTTP_FINAL_URL=%{url_effective}\nTLS_VERIFY=%{ssl_verify_result}\n' \
  "https://$DOMAIN/" || true

if grep -Eqi 'Hotel Prime|Proceed to checkout|Make Booking|Сделать заказ' "$HTML"; then
  echo "PUBLIC_RUNTIME=LEGACY_CONFIRMED"
else
  echo "PUBLIC_RUNTIME=UNKNOWN"
fi

echo "HOSTING_HEADERS_BEGIN"
tr -d '\r' < "$HEADERS" |
  grep -Ei '^(HTTP/|server:|x-powered-by:|via:|location:|x-cache:|cf-ray:)' |
  tail -30 || true
echo "HOSTING_HEADERS_END"

echo
echo "=== TLS CERTIFICATE ==="
timeout 15 openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" </dev/null 2>/dev/null |
  openssl x509 -noout -subject -issuer -dates 2>/dev/null || true

echo
echo "=== LOCAL HOSTING DISCOVERY ==="
echo "LOCAL_WEB_SERVICES_BEGIN"
systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null |
  grep -Ei 'nginx|apache|httpd|caddy|traefik|docker|podman|pm2|ak.?bermet' || echo "NONE_FOUND"
echo "LOCAL_WEB_SERVICES_END"

echo "LOCAL_WEB_CONTAINERS_BEGIN"
docker ps --format '{{.Names}} | {{.Image}} | {{.Ports}}' 2>/dev/null |
  grep -Ei 'ak.?bermet|nginx|caddy|traefik|next|node' || echo "NONE_FOUND"
echo "LOCAL_WEB_CONTAINERS_END"

echo "LOCAL_LISTENERS_80_443_BEGIN"
ss -ltnp 2>/dev/null | grep -E ':(80|443)[[:space:]]' || echo "NONE_FOUND"
echo "LOCAL_LISTENERS_80_443_END"

echo
echo "=== RESULT ==="
if [[ "$RESTORE_RC" -eq 0 ]]; then
  echo "RESTORE_GATE=PASS"
else
  echo "RESTORE_GATE=NEEDS_REVIEW"
fi
echo "DEPLOYMENT_DISCOVERY=COLLECTED"
echo "PRODUCTION_CHANGED=NO"
echo "AK_BERMET_BIG_GATE_2_V3=DONE"
