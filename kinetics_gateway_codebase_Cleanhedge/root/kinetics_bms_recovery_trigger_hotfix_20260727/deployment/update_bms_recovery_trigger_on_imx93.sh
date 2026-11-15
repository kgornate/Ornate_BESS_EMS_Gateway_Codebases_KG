#!/bin/sh
set -eu

APP=/opt/kinetics-gateway/backend
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_bms_recovery_trigger_backup_${STAMP}

mkdir -p "$BACKUP/app/services"
cp "$APP/app/services/bms_pcs_control.py" "$BACKUP/app/services/bms_pcs_control.py"

restore() {
    echo "Update failed; restoring backup from $BACKUP"
    cp "$BACKUP/app/services/bms_pcs_control.py" "$APP/app/services/bms_pcs_control.py"
    systemctl restart kinetics-gateway.service || true
}
trap restore HUP INT TERM

cp "$SRC_DIR/app/services/bms_pcs_control.py" "$APP/app/services/bms_pcs_control.py"

if ! /opt/kinetics-gateway/venv/bin/python -m py_compile "$APP/app/services/bms_pcs_control.py"; then
    restore
    exit 1
fi

systemctl restart kinetics-gateway.service
sleep 3
systemctl is-active --quiet kinetics-gateway.service

trap - HUP INT TERM

echo "BMS recovery trigger hotfix installed successfully."
echo "Backup: $BACKUP"
echo "Register 0x3047 now writes trigger value 1 only; no explicit write of 0."
echo "No BMS or PCS hardware command was sent by this installer."
