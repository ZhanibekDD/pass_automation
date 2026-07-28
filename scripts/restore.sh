#!/usr/bin/env bash
# Восстановление AI SQLite из бекапа.
# Использование: ./scripts/restore.sh <backup-file> [путь-к-бд]
set -euo pipefail

BACKUP_FILE="${1:?Укажите файл бекапа: ./scripts/restore.sh backup.sqlite3}"
DB_PATH="${2:-/opt/pass_automation/data/ai/pass_docs_ai.sqlite3}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "RESTORE ERROR: файл не найден: $BACKUP_FILE" >&2
    exit 1
fi

# Проверка целостности бекапа
echo "RESTORE: проверка целостности $BACKUP_FILE..."
sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check" | grep -q "ok" || {
    echo "RESTORE ERROR: бекап повреждён" >&2
    exit 1
}

# Остановить сервис перед восстановлением (если запущен)
if systemctl is-active --quiet pass-docs-ai 2>/dev/null; then
    echo "RESTORE: останавливаю pass-docs-ai..."
    systemctl stop pass-docs-ai
    RESTART=1
fi

# Сохранить текущую БД рядом
if [ -f "$DB_PATH" ]; then
    CURRENT_BACKUP="${DB_PATH}.before-restore.$(date +%Y%m%d_%H%M%S)"
    cp "$DB_PATH" "$CURRENT_BACKUP"
    echo "RESTORE: текущая БД сохранена в $CURRENT_BACKUP"
fi

cp "$BACKUP_FILE" "$DB_PATH"
chmod 600 "$DB_PATH"
echo "RESTORE OK: $DB_PATH восстановлен из $BACKUP_FILE"

if [ "${RESTART:-0}" = "1" ]; then
    systemctl start pass-docs-ai
    echo "RESTORE: pass-docs-ai перезапущен"
fi
