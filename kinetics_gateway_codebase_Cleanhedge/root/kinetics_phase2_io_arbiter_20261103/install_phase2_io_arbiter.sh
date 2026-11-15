#!/bin/sh
set -eu

PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND=/opt/kinetics-gateway/backend
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/root/kinetics_phase2_io_arbiter_backup_${STAMP}.tar.gz
SCHEDULER_WAS_ACTIVE=no

if [ "${CONFIRM_IDLE:-}" != "YES" ]; then
    echo "Refusing to install without CONFIRM_IDLE=YES. This patch never starts charging/discharging."
    exit 2
fi

echo "Checking exact post-Phase-1 Nagpur baseline..."
(cd / && sha256sum -c "$PACKAGE_DIR/BASELINE_SHA256SUMS")

if systemctl is-active --quiet bess-seven-day-v302.service; then
    SCHEDULER_WAS_ACTIVE=yes
fi
systemctl stop bess-seven-day-v302.service

echo "Creating rollback archive: $BACKUP"
tar -czf "$BACKUP" \
    "$BACKEND/app/protocols/modbus_rtu.py" \
    "$BACKEND/app/protocols/modbus_tcp.py" \
    "$BACKEND/app/assets/pcs_driver.py" \
    "$BACKEND/app/assets/bms_driver.py" \
    "$BACKEND/app/services/gateway_service.py" \
    "$BACKEND/app/services/bms_pcs_control.py" \
    "$BACKEND/tests/test_bms_pcs_control.py"

rollback() {
    status=$?
    if [ "$status" -ne 0 ]; then
        echo "Phase 2 installation failed; restoring pre-patch files..."
        tar -xzf "$BACKUP" -C /
        systemctl restart kinetics-gateway.service || true
        if [ "$SCHEDULER_WAS_ACTIVE" = yes ]; then
            systemctl start bess-seven-day-v302.service || true
        fi
    fi
    exit "$status"
}
trap rollback EXIT INT TERM

systemctl stop kinetics-gateway.service
cp -a "$PACKAGE_DIR/payload/opt/kinetics-gateway/backend/." "$BACKEND/"

cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m compileall -q app
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
    tests/test_io_arbiter_phase2.py \
    tests/test_bms_pcs_control.py \
    tests/test_scheduler_phase1_contract.py \
    tests/test_scheduler_storage.py::test_gateway_close_releases_owned_resources

systemctl restart kinetics-gateway.service
ready=no
for i in $(seq 1 36); do
    if curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:8000/api/health >/tmp/phase2_health.json 2>/dev/null; then
        ready=yes
        break
    fi
    sleep 5
done
[ "$ready" = yes ]

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
health = json.load(open('/tmp/phase2_health.json', encoding='utf-8'))
assert health['status'] in {'ok', 'degraded'}
assert 'io_arbiter' in health
assert 'bms' in health['io_arbiter'] and 'pcs' in health['io_arbiter']
print('Phase 2 health/arbiter diagnostics verified')
PY

if [ "$SCHEDULER_WAS_ACTIVE" = yes ]; then
    systemctl start bess-seven-day-v302.service
fi

trap - EXIT INT TERM
echo "Phase 2 I/O arbiter patch installed successfully."
echo "Rollback archive: $BACKUP"
echo "Scheduler restored to previous active state: $SCHEDULER_WAS_ACTIVE"
