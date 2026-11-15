#!/bin/sh
set -eu

APP=/opt/kinetics-gateway/backend
SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_bms_fault_gate_backup_${STAMP}

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
sleep 5
systemctl is-active --quiet kinetics-gateway.service

trap - HUP INT TERM

echo "Documented BMS fault-gate patch installed successfully."
echo "Backup: $BACKUP"
echo "0x1001 summary bit is retained for diagnostics but no longer blocks by itself."
echo "Documented detailed critical-fault registers remain hard blockers."
echo "Charge/discharge prohibition bits and BMS current limits remain enforced."
echo "No BMS or PCS hardware command was sent by this installer."
