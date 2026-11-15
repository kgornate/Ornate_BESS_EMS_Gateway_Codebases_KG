#!/bin/bash
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
BACKEND=/opt/kinetics-gateway/backend
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/kinetics_phase5_6_safe_stop_reconciliation_backup_${STAMP}.tar.gz"
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
  "$BACKEND/app/services/bms_pcs_control.py" \
  "$BACKEND/tests/test_bms_pcs_control.py"
restore() {
  tar -xzf "$BACKUP" -C /
  systemctl restart kinetics-gateway.service || true
  [[ "$SCHEDULER_WAS_ACTIVE" == no ]] || systemctl restart bess-seven-day-v302.service || true
}
trap restore ERR
cp -a "$PKG/payload/opt/kinetics-gateway/backend/." "$BACKEND/."
cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m compileall -q app
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_bms_pcs_control.py tests/test_phase5_5_control_cache.py \
  tests/test_http_admission.py tests/test_scheduler_phase1_contract.py
systemctl restart kinetics-gateway.service
READY=no
for _ in $(seq 1 300); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/tmp/phase5_6_health.json; then
    if /opt/kinetics-gateway/venv/bin/python -c 'import json; d=json.load(open("/tmp/phase5_6_health.json")); raise SystemExit(0 if (d.get("startup") or {}).get("ready") is True else 1)'; then READY=yes; break; fi
  fi
  sleep 2
done
[[ "$READY" == yes ]] || false
[[ "$SCHEDULER_WAS_ACTIVE" == no ]] || systemctl restart bess-seven-day-v302.service
trap - ERR
echo "Phase 5.6 safe-stop reconciliation patch installed successfully."
echo "Rollback archive: $BACKUP"
