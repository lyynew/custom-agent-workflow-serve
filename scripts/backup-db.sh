#!/bin/sh
# Dump the whole database (Aegra's `public` schema + the `biz` schema) from the
# running compose stack. pg_dump takes a consistent MVCC snapshot, so the
# service can keep serving during the backup.
#
#   ./scripts/backup-db.sh
#   BACKUP_DIR=/var/backups/ai RETENTION_DAYS=14 ./scripts/backup-db.sh
set -eu

BACKUP_DIR=${BACKUP_DIR:-./backups}
RETENTION_DAYS=${RETENTION_DAYS:-7}
SERVICE=${POSTGRES_SERVICE:-postgres}
DB=${POSTGRES_DB:-ai_service}
DB_USER=${POSTGRES_USER:-aegra}

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
FILE="$BACKUP_DIR/${DB}-${STAMP}.sql.gz"

echo "[backup] dumping $DB from service '$SERVICE'"
docker compose exec -T "$SERVICE" pg_dump -U "$DB_USER" -d "$DB" --clean --if-exists \
    | gzip -9 > "$FILE"

# A truncated gzip is worse than no backup: verify before reporting success.
if ! gzip -t "$FILE" 2>/dev/null; then
    echo "[backup] verification failed, removing $FILE" >&2
    rm -f "$FILE"
    exit 1
fi

SIZE=$(wc -c < "$FILE" | tr -d ' ')
if [ "$SIZE" -lt 1024 ]; then
    echo "[backup] dump suspiciously small (${SIZE} bytes), keeping for inspection" >&2
fi

find "$BACKUP_DIR" -name "${DB}-*.sql.gz" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

echo "[backup] wrote $FILE (${SIZE} bytes); retention ${RETENTION_DAYS}d"
