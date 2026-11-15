#!/bin/bash
set -euo pipefail

PACKAGE_DIR=$(cd "$(dirname "$0")" && pwd)
BACKEND=/opt/kinetics-gateway/backend
SERVICE=kinetics-gateway.service
SCHEDULER=bess-seven-day-v302.service
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/kinetics_phase5_v2_control_architecture_backup_${STAMP}.tar.gz

restore_on_error() {
  rc=$?
  echo "Phase 5 v2 installation failed; restoring original files..." >&2
  if [ -f "$BACKUP" ]; then tar -xzf "$BACKUP" -C /; fi
  systemctl restart "$SERVICE" || true
  exit "$rc"
}
trap restore_on_error ERR

if [ "${CONFIRM_IDLE:-}" != YES ]; then
  echo "Refusing installation: confirm all pairs are safely stopped, then use CONFIRM_IDLE=YES." >&2
  exit 2
fi
if systemctl is-active --quiet "$SCHEDULER"; then
  echo "Refusing installation: $SCHEDULER must remain inactive." >&2
  exit 3
fi

echo "Checking exact post-Phase-4 gateway baseline..."
(cd / && sha256sum -c "$PACKAGE_DIR/BASELINE_SHA256SUMS")
(cd "$PACKAGE_DIR" && sha256sum -c PACKAGE_SHA256SUMS)

echo "Creating rollback archive: $BACKUP"
tar -czf "$BACKUP" \
  "$BACKEND/app/services/bms_pcs_control.py" \
  "$BACKEND/tests/test_bms_pcs_control.py"

systemctl stop "$SERVICE"
install -m 0644 "$PACKAGE_DIR/payload/opt/kinetics-gateway/backend/app/services/bms_pcs_control.py" \
  "$BACKEND/app/services/bms_pcs_control.py"
install -m 0644 "$PACKAGE_DIR/payload/opt/kinetics-gateway/backend/tests/test_bms_pcs_control.py" \
  "$BACKEND/tests/test_bms_pcs_control.py"

cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m py_compile app/services/bms_pcs_control.py
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_bms_pcs_control.py \
  tests/test_api.py::test_scheduler_control_view_and_authoritative_compact_refresh \
  tests/test_scheduler_storage.py::test_control_overview_does_not_wait_for_main_gateway_cache_lock \
  tests/test_io_arbiter_phase2.py \
  tests/test_command_reliability_phase3.py

systemctl restart "$SERVICE"
for _ in $(seq 1 36); do
  curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 5
done
curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:8000/api/health >/dev/null
bash "$PACKAGE_DIR/verify_phase5_v2_control_architecture.sh"

trap - ERR
echo "Phase 5 v2 control-architecture patch installed successfully."
echo "Rollback archive: $BACKUP"
echo "Apply the Flutter source update separately with apply_flutter_phase5_v2.ps1."
