#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root" >&2
    exit 1
fi

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=/opt/kinetics-gateway/backend/app/central_sync
PYTHON=/opt/kinetics-gateway/venv/bin/python
SERVICE=kinetics-central-sync.service
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/cleanedge-central-sync-v1.0.3-backup-$STAMP
FILES="general_asset_producer.py uploader.py"
SUCCESS=0

test -d "$TARGET"
test -x "$PYTHON"
mkdir -p "$BACKUP"

for name in $FILES; do
    test -f "$PACKAGE_DIR/app/central_sync/$name"
    test -f "$TARGET/$name"
    cp -a "$TARGET/$name" "$BACKUP/$name"
done

rollback() {
    echo "Installation failed; restoring $BACKUP" >&2
    for name in $FILES; do
        cp -a "$BACKUP/$name" "$TARGET/$name"
    done
    systemctl restart "$SERVICE" || true
}
trap 'if [ "$SUCCESS" -ne 1 ]; then rollback; fi' EXIT HUP INT TERM

systemctl stop "$SERVICE"

for name in $FILES; do
    install -m 0644 "$PACKAGE_DIR/app/central_sync/$name" "$TARGET/$name"
done

"$PYTHON" -m compileall -q "$TARGET"
PYTHONPATH=/opt/kinetics-gateway/backend \
    "$PYTHON" "$PACKAGE_DIR/deployment/validate_patch.py"

systemctl start "$SERVICE"
sleep 5
systemctl is-active --quiet "$SERVICE"

SUCCESS=1
trap - EXIT HUP INT TERM
echo "CleanEdge Central Sync v1.0.3 contract/recovery patch installed"
echo "Backup: $BACKUP"
echo "Existing live outbox and SD archive databases were preserved"
echo "Cloud command policy and gateway control services were not changed"
