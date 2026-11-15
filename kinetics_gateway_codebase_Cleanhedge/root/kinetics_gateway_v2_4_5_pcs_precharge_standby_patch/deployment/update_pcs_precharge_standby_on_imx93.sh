#!/bin/sh
set -eu

BACKEND=/opt/kinetics-gateway/backend
SERVICE=kinetics-gateway.service
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PATCH_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_ROOT=/opt/kinetics-gateway/backups/v2_4_5_pcs_precharge_standby_$STAMP

if [ ! -d "$BACKEND" ]; then
  echo "ERROR: backend directory not found: $BACKEND" >&2
  exit 1
fi

for file in \
  app/services/bms_pcs_control.py \
  app/api/routes.py \
  tools/control_sequence_cli.py \
  generated_protocols/pcs_catalog.json; do
  if [ ! -f "$PATCH_ROOT/$file" ]; then
    echo "ERROR: patch file missing: $PATCH_ROOT/$file" >&2
    exit 1
  fi
done

echo "Stopping $SERVICE..."
systemctl stop "$SERVICE"

mkdir -p "$BACKUP_ROOT/app/services" "$BACKUP_ROOT/app/api" \
  "$BACKUP_ROOT/tools" "$BACKUP_ROOT/generated_protocols"
cp -a "$BACKEND/app/services/bms_pcs_control.py" "$BACKUP_ROOT/app/services/"
cp -a "$BACKEND/app/api/routes.py" "$BACKUP_ROOT/app/api/"
cp -a "$BACKEND/tools/control_sequence_cli.py" "$BACKUP_ROOT/tools/"
cp -a "$BACKEND/generated_protocols/pcs_catalog.json" "$BACKUP_ROOT/generated_protocols/"

echo "Backup created at: $BACKUP_ROOT"

install -m 0644 "$PATCH_ROOT/app/services/bms_pcs_control.py" "$BACKEND/app/services/bms_pcs_control.py"
install -m 0644 "$PATCH_ROOT/app/api/routes.py" "$BACKEND/app/api/routes.py"
install -m 0755 "$PATCH_ROOT/tools/control_sequence_cli.py" "$BACKEND/tools/control_sequence_cli.py"
install -m 0644 "$PATCH_ROOT/generated_protocols/pcs_catalog.json" "$BACKEND/generated_protocols/pcs_catalog.json"

rm -rf "$BACKEND/app/services/__pycache__" "$BACKEND/app/api/__pycache__" "$BACKEND/tools/__pycache__"

PYTHON=/opt/kinetics-gateway/venv/bin/python
if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi

"$PYTHON" -m compileall -q "$BACKEND/app" "$BACKEND/tools"

echo "Restarting $SERVICE..."
systemctl restart "$SERVICE"
sleep 2
systemctl --no-pager --full status "$SERVICE" | sed -n '1,25p'

echo
echo "Verifying new CLI command..."
"$PYTHON" "$BACKEND/tools/control_sequence_cli.py" -h | grep 'prepare-pcs-standby'

echo
echo "V2.4.5 patch installed successfully."
echo "No PCS or BMS hardware write was sent by this installer."
echo "Manual BMS insulation command 0x0401 is now blocked."
echo "Backup: $BACKUP_ROOT"
