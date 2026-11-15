#!/bin/bash
set -euo pipefail

PACKAGE_DIR=$(cd "$(dirname "$0")" && pwd)
BACKEND=/opt/kinetics-gateway/backend
SERVICE=kinetics-gateway.service
SCHEDULER=bess-seven-day-v302.service
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/kinetics_phase4_flutter_control_performance_backup_${STAMP}.tar.gz
SCHEDULER_WAS_ACTIVE=no

fail_restore() {
  rc=$?
  echo "Phase 4 installation failed; restoring source files..." >&2
  if [ -f "$BACKUP" ]; then
    tar -xzf "$BACKUP" -C /
  fi
  systemctl restart "$SERVICE" || true
  if [ "$SCHEDULER_WAS_ACTIVE" = yes ]; then
    systemctl start "$SCHEDULER" || true
  fi
  exit "$rc"
}
trap fail_restore ERR

if [ "${CONFIRM_IDLE:-}" != YES ]; then
  echo "Refusing installation: run with CONFIRM_IDLE=YES after confirming the plant is safely stopped." >&2
  exit 2
fi

if systemctl is-active --quiet "$SCHEDULER"; then
  echo "Refusing installation: $SCHEDULER must be inactive." >&2
  exit 3
fi

echo "Checking exact post-Phase-3C live baseline..."
(cd / && sha256sum -c "$PACKAGE_DIR/BASELINE_SHA256SUMS")
(cd "$PACKAGE_DIR" && sha256sum -c PACKAGE_SHA256SUMS)

echo "Creating rollback archive: $BACKUP"
tar -czf "$BACKUP" \
  /opt/kinetics-gateway/backend/app/services/gateway_service.py \
  /opt/kinetics-gateway/backend/app/storage/sqlite_store.py \
  /opt/kinetics-gateway/backend/tests/test_scheduler_storage.py

systemctl stop "$SERVICE"

echo "Seeding exact persistent storage counters once (large telemetry scan; please wait)..."
KINETICS_CONFIG=/etc/kinetics-gateway/config.json \
  /opt/kinetics-gateway/venv/bin/python "$PACKAGE_DIR/seed_storage_counters.py"

install -m 0644 "$PACKAGE_DIR/payload/opt/kinetics-gateway/backend/app/services/gateway_service.py" \
  "$BACKEND/app/services/gateway_service.py"
install -m 0644 "$PACKAGE_DIR/payload/opt/kinetics-gateway/backend/app/storage/sqlite_store.py" \
  "$BACKEND/app/storage/sqlite_store.py"
install -m 0644 "$PACKAGE_DIR/payload/opt/kinetics-gateway/backend/tests/test_scheduler_storage.py" \
  "$BACKEND/tests/test_scheduler_storage.py"

cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_scheduler_storage.py \
  tests/test_api.py::test_scheduler_control_view_and_authoritative_compact_refresh \
  tests/test_scheduler_phase1_contract.py \
  tests/test_io_arbiter_phase2.py \
  tests/test_command_reliability_phase3.py \
  tests/test_solar_phase3b.py \
  tests/test_solar_grid_meter_phase3c.py

systemctl restart "$SERVICE"

for _ in $(seq 1 36); do
  if curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:8000/api/health >/dev/null

bash "$PACKAGE_DIR/verify_phase4_flutter_control_performance.sh"

trap - ERR
echo "Phase 4 Flutter control-performance patch installed successfully."
echo "Rollback archive: $BACKUP"
echo "Scheduler restored to previous active state: $SCHEDULER_WAS_ACTIVE"
