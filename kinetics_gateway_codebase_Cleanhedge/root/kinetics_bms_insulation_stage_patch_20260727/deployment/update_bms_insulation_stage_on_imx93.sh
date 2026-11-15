#!/bin/sh
set -eu

BACKEND=/opt/kinetics-gateway/backend
PYTHON=/opt/kinetics-gateway/venv/bin/python
PATCH_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_bms_insulation_stage_backup_${STAMP}

if [ ! -d "$BACKEND" ]; then
    echo "Backend not found: $BACKEND" >&2
    exit 1
fi

mkdir -p "$BACKUP/app/services" "$BACKUP/app/api" "$BACKUP/tools"
cp "$BACKEND/app/services/bms_pcs_control.py" "$BACKUP/app/services/"
cp "$BACKEND/app/api/routes.py" "$BACKUP/app/api/"
cp "$BACKEND/tools/control_sequence_cli.py" "$BACKUP/tools/"

restore_backup() {
    echo "Update failed; restoring backup from $BACKUP" >&2
    cp "$BACKUP/app/services/bms_pcs_control.py" "$BACKEND/app/services/"
    cp "$BACKUP/app/api/routes.py" "$BACKEND/app/api/"
    cp "$BACKUP/tools/control_sequence_cli.py" "$BACKEND/tools/"
    systemctl restart kinetics-gateway.service || true
}
trap restore_backup HUP INT TERM EXIT

cp "$PATCH_DIR/app/services/bms_pcs_control.py" "$BACKEND/app/services/"
cp "$PATCH_DIR/app/api/routes.py" "$BACKEND/app/api/"
cp "$PATCH_DIR/tools/control_sequence_cli.py" "$BACKEND/tools/"
chmod 755 "$BACKEND/tools/control_sequence_cli.py"

"$PYTHON" -m py_compile \
    "$BACKEND/app/services/bms_pcs_control.py" \
    "$BACKEND/app/api/routes.py" \
    "$BACKEND/tools/control_sequence_cli.py"

systemctl restart kinetics-gateway.service
sleep 12
systemctl is-active --quiet kinetics-gateway.service

"$PYTHON" "$BACKEND/tools/control_sequence_cli.py" -h | grep -q start-insulation
"$PYTHON" "$BACKEND/tools/control_sequence_cli.py" -h | grep -q verify-insulation

trap - HUP INT TERM EXIT

echo "BMS insulation-sampling stage patch installed successfully."
echo "Backup: $BACKUP"
echo "New guarded command: start-insulation -> Rack BCU absolute Modbus 0x0401 = 1."
echo "The command is not explicitly cleared to 0 and cannot be retriggered for at least 3 seconds."
echo "No BMS or PCS hardware command was sent by this installer."
