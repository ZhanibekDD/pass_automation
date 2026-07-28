#!/usr/bin/env bash
# Резервное копирование AI SQLite.
# Использование: ./scripts/backup.sh [путь-к-бд] [папка-бекапов]
set -euo pipefail

DB_PATH="${1:-/opt/pass_automation/data/ai/pass_docs_ai.sqlite3}"
BACKUP_DIR="${2:-/opt/pass_automation/backups/ai}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/pass_docs_ai_${TIMESTAMP}.sqlite3"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
    echo "BACKUP: БД не найдена: $DB_PATH" >&2
    exit 1
fi

# Онлайн-бекап через SQLite .backup (WAL-safe)
sqlite3 "$DB_PATH" ".backup '${BACKUP_FILE}'"
chmod 600 "$BACKUP_FILE"

echo "BACKUP OK: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"

# Удалить бекапы старше 30 дней
find "$BACKUP_DIR" -name "pass_docs_ai_*.sqlite3" -mtime +30 -delete
echo "BACKUP: старые файлы удалены (> 30 дней)"
