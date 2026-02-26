#!/bin/bash
set -euo pipefail

# Daily SQLite backup to S3
BACKUP_DIR="/tmp/polybot-backup"
DB_PATH="/home/$USER/polybot/data/polybot.db"
S3_BUCKET="${S3_BACKUP_BUCKET:-polybot-backups}"
DATE=$(date +%Y-%m-%d_%H%M)

mkdir -p "$BACKUP_DIR"

# Use SQLite backup command for consistency
if [ -f "$DB_PATH" ]; then
    sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/polybot_$DATE.db'"
    gzip "$BACKUP_DIR/polybot_$DATE.db"
    aws s3 cp "$BACKUP_DIR/polybot_$DATE.db.gz" "s3://$S3_BUCKET/backups/polybot_$DATE.db.gz"
    echo "$(date): Backup uploaded to s3://$S3_BUCKET/backups/polybot_$DATE.db.gz"

    # Clean up old backups (keep last 30)
    aws s3 ls "s3://$S3_BUCKET/backups/" | sort | head -n -30 | awk '{print $4}' | while read -r file; do
        aws s3 rm "s3://$S3_BUCKET/backups/$file"
    done
fi

rm -rf "$BACKUP_DIR"
