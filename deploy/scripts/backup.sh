#!/bin/bash
set -euo pipefail

# Daily SQLite backup — WAL-safe via Python checkpoint + copy
BACKUP_DIR="/tmp/polybot-backup"
LOCAL_BACKUP_DIR="/home/${USER:-ubuntu}/polybot/backups"
S3_BUCKET="${S3_BACKUP_BUCKET:-}"
DATE=$(date +%Y-%m-%d_%H%M)

mkdir -p "$BACKUP_DIR" "$LOCAL_BACKUP_DIR"

# WAL-safe backup: checkpoint WAL into main DB, then copy the file
# This uses Python (always available in the container) instead of sqlite3 CLI
if ! docker exec polybot-app python3 -c "
import sqlite3, shutil
conn = sqlite3.connect('/app/data/polybot.db')
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
shutil.copy2('/app/data/polybot.db', '/tmp/backup.db')
" 2>/dev/null; then
    echo "$(date): ERROR — docker exec backup failed"
    exit 1
fi

docker cp polybot-app:/tmp/backup.db "$BACKUP_DIR/polybot_$DATE.db"
docker exec polybot-app rm -f /tmp/backup.db

# Compress
gzip "$BACKUP_DIR/polybot_$DATE.db"

# Always save locally
cp "$BACKUP_DIR/polybot_$DATE.db.gz" "$LOCAL_BACKUP_DIR/"
echo "$(date): Local backup saved to $LOCAL_BACKUP_DIR/polybot_$DATE.db.gz"

# Keep last 30 local copies
ls -t "$LOCAL_BACKUP_DIR"/polybot_*.db.gz 2>/dev/null | tail -n +31 | xargs -r rm -f

# Try S3 upload (optional — skip if no AWS CLI or bucket)
if command -v aws &>/dev/null && [ -n "$S3_BUCKET" ]; then
    if aws s3 cp "$BACKUP_DIR/polybot_$DATE.db.gz" "s3://$S3_BUCKET/backups/polybot_$DATE.db.gz" 2>/dev/null; then
        echo "$(date): S3 backup uploaded to s3://$S3_BUCKET/backups/"
        # Clean old S3 backups (keep last 30)
        aws s3 ls "s3://$S3_BUCKET/backups/" | sort | head -n -30 | awk '{print $4}' | while read -r file; do
            aws s3 rm "s3://$S3_BUCKET/backups/$file" 2>/dev/null || true
        done
    else
        echo "$(date): WARNING — S3 upload failed, local backup preserved"
    fi
fi

rm -rf "$BACKUP_DIR"
