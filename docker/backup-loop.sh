#!/bin/sh
set -eu

mkdir -p /backups

while true; do
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  temporary="/backups/.cellier-${timestamp}.sql.gz.tmp"
  destination="/backups/cellier-${timestamp}.sql.gz"
  pg_dump --format=plain --no-owner --no-privileges \
    --host="${PGHOST}" --username="${PGUSER}" "${PGDATABASE}" \
    | gzip -9 > "${temporary}"
  mv "${temporary}" "${destination}"
  media_temporary="/backups/.cellier-media-${timestamp}.tar.gz.tmp"
  media_destination="/backups/cellier-media-${timestamp}.tar.gz"
  tar -czf "${media_temporary}" -C /media .
  mv "${media_temporary}" "${media_destination}"
  find /backups -type f -name 'cellier-*.sql.gz' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
  find /backups -type f -name 'cellier-media-*.tar.gz' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
  sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
done
