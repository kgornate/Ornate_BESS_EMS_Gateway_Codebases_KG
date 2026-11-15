#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_$(date +%Y%m%d_%H%M%S)"

if [[ "${CONFIRM_IDLE:-}" != "YES" ]]; then
  echo "Refusing installation: first Safe Stop All and verify every pair is stopped, then run with CONFIRM_IDLE=YES."
  exit 2
fi

baseline_check() {
  local expected="$1" path="$2"
  [[ -f "$path" ]] || { echo "Missing deployed baseline: $path"; exit 3; }
  local actual
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || { echo "Baseline mismatch: $path"; echo "Expected $expected"; echo "Actual   $actual"; exit 3; }
}

baseline_check d2c443ab6409841af246b7f1642fd0446f2eb2a6d3d07dd6c1f48941a7e905f5 /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
baseline_check bf442e80ab6c6d15712e484be640b3372b1b585d406c802d3d85c226d5543a5d /opt/kinetics-gateway/backend/app/services/gateway_service.py
baseline_check b10bea568f7436109bfe7fddd7b1f61abb49e5669f193e6ed82d628befdfe814 /opt/kinetics-gateway/backend/app/api/routes.py
baseline_check a2d73c36c6ef86aea3e7864eed6fcb28ec13a8754cbf34985d18278f86dfb3f3 /opt/kinetics-gateway/backend/app/main.py
baseline_check bad432ebef4218d6a727ee07e8453479a8991bdc7e214cad2ab416b2538a1a1d /opt/kinetics-gateway/backend/app/services/http_admission.py

systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
mkdir -p "$BACKUP_DIR/backend/app/services" "$BACKUP_DIR/backend/app/api" "$BACKUP_DIR/root" "$BACKUP_DIR/systemd"
cp -a /opt/kinetics-gateway/backend/app/services/{bms_pcs_control.py,gateway_service.py,http_admission.py} "$BACKUP_DIR/backend/app/services/"
[[ ! -f /opt/kinetics-gateway/backend/app/services/automation_schedule.py ]] || cp -a /opt/kinetics-gateway/backend/app/services/automation_schedule.py "$BACKUP_DIR/backend/app/services/"
cp -a /opt/kinetics-gateway/backend/app/api/routes.py "$BACKUP_DIR/backend/app/api/"
cp -a /opt/kinetics-gateway/backend/app/main.py "$BACKUP_DIR/backend/app/"
[[ ! -f /root/bess_solar_time_automation.py ]] || cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"
[[ ! -f /etc/systemd/system/bess-seven-day-v302.service ]] || cp -a /etc/systemd/system/bess-seven-day-v302.service "$BACKUP_DIR/systemd/"

cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/." /opt/kinetics-gateway/backend/
cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/bess_solar_time_automation.py
cp -a "$ROOT_DIR/payload/etc/systemd/system/bess-seven-day-v302.service" /etc/systemd/system/bess-seven-day-v302.service
chmod 755 /root/bess_solar_time_automation.py

mkdir -p /mnt/ems-logs/kinetics-gateway
if [[ ! -f /mnt/ems-logs/kinetics-gateway/automation_schedule.json ]]; then
  cp -a "$ROOT_DIR/default_automation_schedule.json" /mnt/ems-logs/kinetics-gateway/automation_schedule.json
  chmod 600 /mnt/ems-logs/kinetics-gateway/automation_schedule.json
fi

cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m py_compile \
  app/services/automation_schedule.py app/services/gateway_service.py app/api/routes.py \
  /root/bess_solar_time_automation.py
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_http_admission.py tests/test_bms_pcs_control.py \
  tests/test_control_snapshot_projection.py tests/test_automation_schedule.py

systemctl daemon-reload
systemctl enable bess-seven-day-v302.service
systemctl start kinetics-gateway.service
for _ in $(seq 1 90); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health | \
    /opt/kinetics-gateway/venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("status")=="ok" and d.get("startup",{}).get("ready") else 1)' 2>/dev/null; then
    break
  fi
  sleep 2
done
curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null
systemctl start bess-seven-day-v302.service
echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_latest
echo "Phase 5.8 installed. Automation service is running with automation disabled by default. Configure and enable it from Flutter."
