#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root" >&2
    exit 1
fi

if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
    echo "Usage: $0 /root/cleanedge-central-sync-v1.0.3-backup-TIMESTAMP" >&2
    exit 1
fi

BACKUP=$1
TARGET=/opt/kinetics-gateway/backend/app/central_sync
SERVICE=kinetics-central-sync.service

systemctl stop "$SERVICE"
install -m 0644 "$BACKUP/general_asset_producer.py" "$TARGET/general_asset_producer.py"
install -m 0644 "$BACKUP/uploader.py" "$TARGET/uploader.py"
/opt/kinetics-gateway/venv/bin/python -m compileall -q "$TARGET"
systemctl start "$SERVICE"
sleep 5
systemctl is-active --quiet "$SERVICE"
echo "Rollback complete from: $BACKUP"
