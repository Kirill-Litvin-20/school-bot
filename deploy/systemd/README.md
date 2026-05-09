# systemd units for school-system

These are reference unit files for the production server. Copy them into
`/etc/systemd/system/`, run `systemctl daemon-reload`, then enable as
required.

## Bot services

If you don't already have systemd units for the two bots — copy the reference
ones from this directory:

```bash
sudo cp deploy/systemd/school-bot.service        /etc/systemd/system/
sudo cp deploy/systemd/school-admin-bot.service  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now school-bot school-admin-bot

# Tail logs (journald):
journalctl -u school-bot -f
journalctl -u school-admin-bot -f
```

`shared/logging_setup.py` detects `INVOCATION_ID` (set by systemd) and
suppresses the local `logs/<app>.log` file by default — journald keeps the
canonical log. If you need the on-disk file for some reason, set
`SCHOOL_LOG_TO_FILE=1` in `/opt/school-system/.env`.

## Bound journald disk usage

Drop the included config so logs don't fill the disk:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp deploy/systemd/journald-school.conf /etc/systemd/journald.conf.d/school.conf
sudo systemctl restart systemd-journald
```

This caps the journal at ~500 MB total / 50 MB per file / 2 weeks.

## Postgres backup

```bash
sudo cp deploy/systemd/school-backup.service /etc/systemd/system/
sudo cp deploy/systemd/school-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now school-backup.timer

# Verify schedule:
systemctl list-timers school-backup.timer
# Run a one-off backup right now:
sudo systemctl start school-backup.service
journalctl -u school-backup -n 50 --no-pager
```

The script reads `DATABASE_URL` from `/opt/school-system/.env` and writes
gzipped dumps to `/var/backups/school-system/school-YYYYMMDD-HHMMSS.sql.gz`,
keeping the last 7 days. Override via env vars:

- `SCHOOL_BACKUP_DIR` — where to put dumps (default `/var/backups/school-system`).
- `SCHOOL_BACKUP_RETENTION_DAYS` — how many days to keep (default `7`).
- `SCHOOL_ENV_FILE` — alternate env file (default `/opt/school-system/.env`).

To restore the latest dump:

```bash
LATEST=$(ls -t /var/backups/school-system/school-*.sql.gz | head -n 1)
gunzip -c "$LATEST" | psql "$DATABASE_URL"
```
