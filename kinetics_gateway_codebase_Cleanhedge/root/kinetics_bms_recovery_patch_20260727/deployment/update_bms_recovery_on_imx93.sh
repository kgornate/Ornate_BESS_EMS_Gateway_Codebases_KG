#!/bin/sh
set -eu

BACKEND=/opt/kinetics-gateway/backend
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PATCH_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_bms_recovery_backup_$STAMP
INSTALLED=0

FILES="app/services/bms_pcs_control.py app/api/routes.py tools/control_sequence_cli.py tests/test_bms_pcs_control.py"

for rel in $FILES; do
    test -f "$PATCH_ROOT/$rel" || { echo "Missing patch file: $rel" >&2; exit 1; }
    test -f "$BACKEND/$rel" || { echo "Missing active backend file: $BACKEND/$rel" >&2; exit 1; }
done

/opt/kinetics-gateway/venv/bin/python -m py_compile \
    "$PATCH_ROOT/app/services/bms_pcs_control.py" \
    "$PATCH_ROOT/app/api/routes.py" \
    "$PATCH_ROOT/tools/control_sequence_cli.py"

mkdir -p "$BACKUP"
for rel in $FILES; do
    mkdir -p "$BACKUP/$(dirname "$rel")"
    cp -a "$BACKEND/$rel" "$BACKUP/$rel"
done

rollback() {
    code=$?
    if [ "$code" -ne 0 ] && [ "$INSTALLED" -eq 1 ]; then
        echo "Update failed; restoring backup from $BACKUP" >&2
        for rel in $FILES; do
            cp -a "$BACKUP/$rel" "$BACKEND/$rel"
        done
        systemctl restart kinetics-gateway.service || true
    fi
    exit "$code"
}
trap rollback EXIT HUP INT TERM

systemctl stop kinetics-gateway.service
for rel in $FILES; do
    cp -a "$PATCH_ROOT/$rel" "$BACKEND/$rel"
done
chmod 755 "$BACKEND/tools/control_sequence_cli.py"
INSTALLED=1

/opt/kinetics-gateway/venv/bin/python -m py_compile \
    "$BACKEND/app/services/bms_pcs_control.py" \
    "$BACKEND/app/api/routes.py" \
    "$BACKEND/tools/control_sequence_cli.py"

cd "$BACKEND"
if /opt/kinetics-gateway/venv/bin/python -c "import pytest" >/dev/null 2>&1; then
    /opt/kinetics-gateway/venv/bin/python -m pytest tests/test_bms_pcs_control.py -q
else
    echo "pytest not installed; skipping on-device tests after successful py_compile."
fi

systemctl restart kinetics-gateway.service
sleep 6
systemctl is-active --quiet kinetics-gateway.service
systemctl status kinetics-gateway.service -l --no-pager

INSTALLED=0
trap - EXIT HUP INT TERM

echo "BMS recovery patch installed successfully."
echo "Backup: $BACKUP"
echo "No hardware command was sent by the installer."
echo "Direct command:"
echo "/opt/kinetics-gateway/venv/bin/python tools/control_sequence_cli.py recover-bms --pair-id pair_1 --confirmation EXECUTE_STAGE_WRITE"
