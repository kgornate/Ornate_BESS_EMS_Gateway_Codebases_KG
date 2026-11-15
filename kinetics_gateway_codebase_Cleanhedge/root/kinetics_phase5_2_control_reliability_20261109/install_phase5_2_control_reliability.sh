#!/bin/bash
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
BACKEND=/opt/kinetics-gateway/backend
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/kinetics_phase5_2_control_reliability_backup_${STAMP}.tar.gz"
SCHEDULER_WAS_ACTIVE=no

[[ "${CONFIRM_IDLE:-}" == YES ]] || { echo "Set CONFIRM_IDLE=YES only after all pairs are stopped."; exit 2; }
cd "$PKG" && sha256sum -c PACKAGE_SHA256SUMS
cd / && sha256sum -c "$PKG/BASELINE_SHA256SUMS"
if systemctl is-active --quiet bess-seven-day-v302.service; then
  SCHEDULER_WAS_ACTIVE=yes
  systemctl stop bess-seven-day-v302.service
fi
systemctl stop kinetics-gateway.service
tar -czf "$BACKUP" \
  /opt/kinetics-gateway/backend/app/main.py \
  /opt/kinetics-gateway/backend/app/services/http_admission.py \
  /opt/kinetics-gateway/backend/app/services/gateway_service.py \
  /opt/kinetics-gateway/backend/app/protocols/modbus_rtu.py \
  /opt/kinetics-gateway/backend/tests/test_http_admission.py \
  /opt/kinetics-gateway/backend/tests/test_command_reliability_phase3.py

restore() {
  echo "Installation failed; restoring $BACKUP"
  tar -xzf "$BACKUP" -C /
  systemctl restart kinetics-gateway.service || true
  [[ "$SCHEDULER_WAS_ACTIVE" == no ]] || systemctl restart bess-seven-day-v302.service || true
}
trap restore ERR
cp -a "$PKG/payload/opt/kinetics-gateway/backend/." "$BACKEND/."
cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m compileall -q app
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_http_admission.py tests/test_command_reliability_phase3.py \
  tests/test_api.py::test_scheduler_control_view_and_authoritative_compact_refresh \
  tests/test_bms_pcs_control.py
systemctl restart kinetics-gateway.service
READY=no
for _ in $(seq 1 300); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/tmp/phase5_2_health.json; then
    if /opt/kinetics-gateway/venv/bin/python -c 'import json; d=json.load(open("/tmp/phase5_2_health.json")); raise SystemExit(0 if (d.get("startup") or {}).get("ready") is True else 1)'; then
      READY=yes
      break
    fi
  fi
  sleep 2
done
[[ "$READY" == yes ]] || { echo "Gateway did not become ready within the startup window"; false; }
/opt/kinetics-gateway/venv/bin/python -c 'import json; d=json.load(open("/tmp/phase5_2_health.json")); print("health:",d.get("status"),"ready:",(d.get("startup") or {}).get("ready"),"assets:",d.get("assets_online"),"/",d.get("assets_total")); assert (d.get("startup") or {}).get("ready") is True'
[[ "$SCHEDULER_WAS_ACTIVE" == no ]] || systemctl restart bess-seven-day-v302.service
trap - ERR
echo "Phase 5.2 control reliability patch installed successfully."
echo "Rollback archive: $BACKUP"
echo "Scheduler restored to previous active state: $SCHEDULER_WAS_ACTIVE"
