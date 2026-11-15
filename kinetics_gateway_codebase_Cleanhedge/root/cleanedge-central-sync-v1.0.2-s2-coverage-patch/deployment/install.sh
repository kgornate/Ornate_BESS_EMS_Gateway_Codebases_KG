#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root" >&2
    exit 1
fi

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=/opt/kinetics-gateway/backend/app/central_sync
PYTHON=/opt/kinetics-gateway/venv/bin/python
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/cleanedge-central-sync-v1.0.2-backup-$STAMP
FILES="cleanedge_normalized.py general_asset_producer.py charge_discharge_producer.py local_gateway_api.py service.py fast_bess_producer.py"
SUCCESS=0

test -d "$TARGET"
test -x "$PYTHON"
mkdir -p "$BACKUP"

for name in $FILES; do
    test -f "$PACKAGE_DIR/app/central_sync/$name"
    cp -a "$TARGET/$name" "$BACKUP/$name"
done

rollback() {
    echo "Installation failed; restoring $BACKUP" >&2
    for name in $FILES; do
        cp -a "$BACKUP/$name" "$TARGET/$name"
    done
    systemctl restart kinetics-central-sync.service || true
}
trap 'if [ "$SUCCESS" -ne 1 ]; then rollback; fi' EXIT HUP INT TERM

systemctl stop kinetics-central-sync.service

for name in $FILES; do
    install -m 0644 "$PACKAGE_DIR/app/central_sync/$name" "$TARGET/$name"
done

"$PYTHON" -m compileall -q "$TARGET"
PYTHONPATH=/opt/kinetics-gateway/backend "$PYTHON" "$PACKAGE_DIR/deployment/validate_patch.py"

systemctl start kinetics-central-sync.service
sleep 5
systemctl is-active --quiet kinetics-central-sync.service

SUCCESS=1
trap - EXIT HUP INT TERM
echo "CleanEdge Central Sync v1.0.2 correctness and S2 coverage patch installed"
echo "Backup: $BACKUP"
echo "Existing outbox/archive messages were preserved unchanged"

