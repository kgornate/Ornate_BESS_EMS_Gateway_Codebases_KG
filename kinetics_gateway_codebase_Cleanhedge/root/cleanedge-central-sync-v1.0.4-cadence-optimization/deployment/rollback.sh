#!/bin/sh
set -eu

BACKUP=${1:-$(cat /root/cleanedge-central-sync-v1.0.4-last-backup)}
TARGET=/opt/kinetics-gateway/backend/configs/cleanedge_s2_policy_frozen_v1_0.json
CONFIG=/etc/kinetics-gateway/central_sync.json

test -f "$BACKUP/cleanedge_s2_policy_frozen_v1_0.json"
test -f "$BACKUP/central_sync.json"
systemctl stop kinetics-central-sync.service
cp -a "$BACKUP/cleanedge_s2_policy_frozen_v1_0.json" "$TARGET"
cp -a "$BACKUP/central_sync.json" "$CONFIG"
systemctl start kinetics-central-sync.service
systemctl is-active --quiet kinetics-central-sync.service
echo "Rolled back CleanEdge cadence configuration from $BACKUP"
