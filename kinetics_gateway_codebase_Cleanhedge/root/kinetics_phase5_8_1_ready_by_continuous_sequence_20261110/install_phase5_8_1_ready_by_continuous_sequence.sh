#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_1_$(date +%Y%m%d_%H%M%S)"

[[ "${CONFIRM_IDLE:-}" == "YES" ]] || { echo "Run Safe Stop All, verify every pair stopped, then use CONFIRM_IDLE=YES."; exit 2; }
check() {
  local expected="$1" path="$2" actual
  [[ -f "$path" ]] || { echo "Missing Phase 5.8 baseline: $path"; exit 3; }
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || { echo "Baseline mismatch: $path"; echo "Expected $expected"; echo "Actual   $actual"; exit 3; }
}
check d2c443ab6409841af246b7f1642fd0446f2eb2a6d3d07dd6c1f48941a7e905f5 /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
check 5360cd99063be6149415f57e34390fe9cb1d749e7936f3003037e7c4a1f995b7 /opt/kinetics-gateway/backend/app/services/gateway_service.py
check 07d0080cc460ae2e448859dcecc6b7531b961a77a197e930aace608855e41255 /opt/kinetics-gateway/backend/app/api/routes.py
check 368a162e93f616adc73a5272d3412b412c4c02e5842a8ebb63d317895dcb3137 /opt/kinetics-gateway/backend/app/services/automation_schedule.py
check aa09e181cdbe74686d0fcc6fbcb2520da3525e472fdcd37343e85aebdc01902c /root/bess_solar_time_automation.py

systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
mkdir -p "$BACKUP_DIR/backend/app/services" "$BACKUP_DIR/backend/app/api" "$BACKUP_DIR/root" "$BACKUP_DIR/systemd"
cp -a /opt/kinetics-gateway/backend/app/services/{bms_pcs_control.py,gateway_service.py,http_admission.py,automation_schedule.py} "$BACKUP_DIR/backend/app/services/"
cp -a /opt/kinetics-gateway/backend/app/api/routes.py "$BACKUP_DIR/backend/app/api/"
cp -a /opt/kinetics-gateway/backend/app/main.py "$BACKUP_DIR/backend/app/"
cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"
cp -a /etc/systemd/system/bess-seven-day-v302.service "$BACKUP_DIR/systemd/"

cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/." /opt/kinetics-gateway/backend/
cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/
cp -a "$ROOT_DIR/payload/etc/systemd/system/bess-seven-day-v302.service" /etc/systemd/system/
chmod 755 /root/bess_solar_time_automation.py

cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m py_compile \
  app/services/automation_schedule.py app/services/bms_pcs_control.py \
  app/services/gateway_service.py app/api/routes.py /root/bess_solar_time_automation.py
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_http_admission.py tests/test_bms_pcs_control.py \
  tests/test_control_snapshot_projection.py tests/test_automation_schedule.py \
  tests/test_ready_by_scheduler.py

systemctl daemon-reload
systemctl start kinetics-gateway.service
for _ in $(seq 1 90); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health | \
    /opt/kinetics-gateway/venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("status")=="ok" and d.get("startup",{}).get("ready") else 1)' 2>/dev/null; then break; fi
  sleep 2
done
curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null
systemctl start bess-seven-day-v302.service
echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_1_latest
echo "Phase 5.8.1 installed. Existing automation configuration is preserved."
