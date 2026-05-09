#!/usr/bin/env bash
# Daily Postgres dump for school-system.
#
# - Reads DATABASE_URL from /opt/school-system/.env (or the file given by
#   $SCHOOL_ENV_FILE).
# - Writes a gzipped pg_dump to $BACKUP_DIR/school-YYYYMMDD-HHMMSS.sql.gz.
# - Removes anything older than RETENTION_DAYS to keep disk usage bounded.
#
# Designed to be wired into a systemd timer (see deploy/systemd/).

set -euo pipefail

ENV_FILE="${SCHOOL_ENV_FILE:-/opt/school-system/.env}"
BACKUP_DIR="${SCHOOL_BACKUP_DIR:-/var/backups/school-system}"
RETENTION_DAYS="${SCHOOL_BACKUP_RETENTION_DAYS:-7}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "[backup] env file not found: $ENV_FILE" >&2
    exit 1
fi

# Pull DATABASE_URL out of the env file without leaking the rest.
DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -E 's/^"(.*)"$/\1/' | sed -E "s/^'(.*)'\$/\\1/")"

if [[ -z "$DATABASE_URL" ]]; then
    echo "[backup] DATABASE_URL is empty in $ENV_FILE — nothing to back up." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/school-$TIMESTAMP.sql.gz"

echo "[backup] dumping to $TARGET"
pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$TARGET"

# Remove old dumps. -mtime +N matches files modified more than N*24h ago.
echo "[backup] pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'school-*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] done"
