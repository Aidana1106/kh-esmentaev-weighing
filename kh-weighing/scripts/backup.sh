#!/usr/bin/env bash
# Ежедневная резервная копия БД (НФТ-06: RPO ≤ 24 ч). Хранит последние 14 копий.
# Запускать из папки проекта. Пример cron (каждый день в 03:30):
#   30 3 * * * cd /opt/kh-weighing && ./scripts/backup.sh >> backups/backup.log 2>&1
# Восстановление:
#   gunzip -c backups/weighing-ДАТА.sql.gz | docker compose exec -T db psql -U weighing -d weighing
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP="${KEEP:-14}"
mkdir -p "$BACKUP_DIR"

file="$BACKUP_DIR/weighing-$(date +%Y-%m-%d_%H%M).sql.gz"
docker compose exec -T db pg_dump -U weighing -d weighing --clean --if-exists | gzip > "$file"

# пустой архив = копия не удалась
if [ "$(gzip -cd "$file" | head -c 100 | wc -c)" -eq 0 ]; then
  echo "$(date -Is) ОШИБКА: пустая копия $file" >&2
  rm -f "$file"
  exit 1
fi

ls -1t "$BACKUP_DIR"/weighing-*.sql.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "$(date -Is) OK $file ($(du -h "$file" | cut -f1))"
