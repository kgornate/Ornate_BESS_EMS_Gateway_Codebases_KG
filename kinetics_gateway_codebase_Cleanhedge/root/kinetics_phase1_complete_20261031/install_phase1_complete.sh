#!/bin/bash
set -Eeuo pipefail

if [ "${CONFIRM_IDLE:-}" != "YES" ]; then
  echo "Refusing installation: confirm that all pairs are idle."
  echo "Run: CONFIRM_IDLE=YES bash install_phase1_complete.sh"
  exit 2
fi

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PAYLOAD_DIR="$PACKAGE_DIR/payload"
GATEWAY_SERVICE=kinetics-gateway.service
SCHEDULER_SERVICE=bess-seven-day-v302.service
PYTHON=/opt/kinetics-gateway/venv/bin/python
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/kinetics_phase1_complete_backup_${STAMP}.tar.gz
SCHEDULER_WAS_ACTIVE=no

echo "Checking exact Nagpur baseline..."
sha256sum -c "$PACKAGE_DIR/BASELINE_SHA256SUMS"

if systemctl is-active --quiet "$SCHEDULER_SERVICE"; then
  SCHEDULER_WAS_ACTIVE=yes
fi

echo "Stopping scheduler first so its existing safe-stop hook can use the gateway..."
if [ "$SCHEDULER_WAS_ACTIVE" = yes ]; then
  systemctl stop "$SCHEDULER_SERVICE"
fi
systemctl stop "$GATEWAY_SERVICE"

echo "Creating rollback archive: $BACKUP"
tar -czf "$BACKUP" \
  /opt/kinetics-gateway/backend/app/api/routes.py \
  /opt/kinetics-gateway/backend/app/assets/bms_driver.py \
  /opt/kinetics-gateway/backend/app/main.py \
  /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py \
  /opt/kinetics-gateway/backend/app/services/gateway_service.py \
  /opt/kinetics-gateway/backend/tests/test_api.py \
  /opt/kinetics-gateway/backend/tests/test_scheduler_storage.py \
  /root/bess_seven_day_v302_compatible_scheduler.py \
  /etc/systemd/system/kinetics-gateway.service
echo "$BACKUP" > /root/kinetics_phase1_complete_latest_backup.txt
echo "$SCHEDULER_WAS_ACTIVE" > /root/kinetics_phase1_complete_scheduler_was_active.txt

rollback_on_error() {
  code=$?
  echo "Installation failed; restoring the pre-patch files..."
  tar -xzf "$BACKUP" -C /
  systemctl daemon-reload
  systemctl start "$GATEWAY_SERVICE" || true
  if [ "$SCHEDULER_WAS_ACTIVE" = yes ]; then
    systemctl start "$SCHEDULER_SERVICE" || true
  fi
  exit "$code"
}
trap rollback_on_error ERR

install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/app/api/routes.py" /opt/kinetics-gateway/backend/app/api/routes.py
install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/app/assets/bms_driver.py" /opt/kinetics-gateway/backend/app/assets/bms_driver.py
install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/app/main.py" /opt/kinetics-gateway/backend/app/main.py
install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/app/services/bms_pcs_control.py" /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/app/services/gateway_service.py" /opt/kinetics-gateway/backend/app/services/gateway_service.py
install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/tests/test_api.py" /opt/kinetics-gateway/backend/tests/test_api.py
install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/tests/test_scheduler_storage.py" /opt/kinetics-gateway/backend/tests/test_scheduler_storage.py
install -m 0644 "$PAYLOAD_DIR/opt/kinetics-gateway/backend/tests/test_scheduler_phase1_contract.py" /opt/kinetics-gateway/backend/tests/test_scheduler_phase1_contract.py
install -m 0700 "$PAYLOAD_DIR/root/bess_seven_day_v302_compatible_scheduler.py" /root/bess_seven_day_v302_compatible_scheduler.py
install -m 0644 "$PAYLOAD_DIR/etc/systemd/system/kinetics-gateway.service" /etc/systemd/system/kinetics-gateway.service

systemctl daemon-reload
cd /opt/kinetics-gateway/backend
"$PYTHON" -m compileall -q app tests /root/bess_seven_day_v302_compatible_scheduler.py
"$PYTHON" -m pytest -q --disable-warnings --tb=short \
  tests/test_api.py::test_scheduler_control_view_and_authoritative_compact_refresh \
  tests/test_scheduler_storage.py::test_scheduler_control_view_is_small_and_contains_soc \
  tests/test_scheduler_storage.py::test_gateway_close_releases_owned_resources \
  tests/test_scheduler_phase1_contract.py

systemctl start "$GATEWAY_SERVICE"
for i in $(seq 1 36); do curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 5; done
systemctl is-active --quiet "$GATEWAY_SERVICE"
curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:8000/api/health >/dev/null

if [ "$SCHEDULER_WAS_ACTIVE" = yes ]; then
  systemctl start "$SCHEDULER_SERVICE"
  systemctl is-active --quiet "$SCHEDULER_SERVICE"
fi

trap - ERR
echo "Phase 1 complete patch installed successfully."
echo "Rollback archive: $BACKUP"
echo "Scheduler restored to previous active state: $SCHEDULER_WAS_ACTIVE"
