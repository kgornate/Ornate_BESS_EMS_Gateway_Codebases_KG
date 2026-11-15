#!/bin/sh
set -eu

[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }
PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=/opt/kinetics-gateway/venv/bin/python
BACKEND=/opt/kinetics-gateway/backend
TARGET=$BACKEND/configs/cleanedge_s2_policy_frozen_v1_0.json
CONFIG=/etc/kinetics-gateway/central_sync.json
SERVICE=kinetics-central-sync.service
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/cleanedge-central-sync-v1.0.5-backup-$STAMP
SUCCESS=0

test -x "$PYTHON"
test -f "$TARGET"
test -f "$CONFIG"
grep -q 'TERMINAL_CONTRACT_REJECTION_CODES' "$BACKEND/app/central_sync/uploader.py" || {
    echo "Refusing install: v1.0.3 contract/recovery fix is missing" >&2; exit 1;
}
mkdir -p "$BACKUP"
cp -a "$TARGET" "$BACKUP/cleanedge_s2_policy_frozen_v1_0.json"
cp -a "$CONFIG" "$BACKUP/central_sync.json"

rollback() {
    echo "Installation failed; restoring $BACKUP" >&2
    cp -a "$BACKUP/cleanedge_s2_policy_frozen_v1_0.json" "$TARGET"
    cp -a "$BACKUP/central_sync.json" "$CONFIG"
    systemctl restart "$SERVICE" || true
}
trap 'if [ "$SUCCESS" -ne 1 ]; then rollback; fi' EXIT HUP INT TERM

systemctl stop "$SERVICE"
install -m 0644 "$PACKAGE_DIR/configs/cleanedge_s2_policy_frozen_v1_0.json" "$TARGET"
TARGET="$TARGET" CONFIG="$CONFIG" "$PYTHON" - <<'PY'
import hashlib, json, os
from pathlib import Path
target=Path(os.environ["TARGET"]); config_path=Path(os.environ["CONFIG"])
digest=hashlib.sha256(target.read_bytes()).hexdigest()
config=json.loads(config_path.read_text(encoding="utf-8"))
config["general_assets"]["s2_policy_expected_sha256"]=digest
config_path.write_text(json.dumps(config,indent=2)+"\n",encoding="utf-8")
print("Pinned v1.0.5 manifest:",digest)
PY
cd "$BACKEND"
PYTHONPATH="$BACKEND" "$PYTHON" "$PACKAGE_DIR/deployment/validate_patch.py"
systemctl start "$SERVICE"
sleep 5
systemctl is-active --quiet "$SERVICE"
SUCCESS=1
trap - EXIT HUP INT TERM
printf '%s\n' "$BACKUP" > /root/cleanedge-central-sync-v1.0.5-last-backup
echo "CleanEdge Central Sync v1.0.5 solar/grid-meter routing installed"
echo "Backup: $BACKUP"
echo "Old combiner_box_meter backlog retained for terminal cleanup"
echo "No polling, cadence, point fields, automation, or cloud command changes"
