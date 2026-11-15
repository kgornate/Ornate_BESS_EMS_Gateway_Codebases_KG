#!/bin/bash
set -euo pipefail

PKG="$(cd "$(dirname "$0")" && pwd)"
BACKEND=/opt/kinetics-gateway/backend
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/kinetics_phase5_7_1_control_truth_safe_stop_progress_backup_${STAMP}.tar.gz"
SCHEDULER_WAS_ACTIVE=no

[[ "${CONFIRM_IDLE:-}" == YES ]] || {
  echo "REFUSED: Phase 5.7.1 activation restarts the gateway."
  echo "Safe-stop every pair, verify PCS power is 0 kW and contactors are open, then run:"
  echo "  CONFIRM_IDLE=YES bash $0"
  exit 2
}

cd "$PKG"
sha256sum -c PACKAGE_SHA256SUMS
cd /
sha256sum -c "$PKG/BASELINE_SHA256SUMS"

tar -czf "$BACKUP" \
  "$BACKEND/app/main.py" \
  "$BACKEND/app/api/routes.py" \
  "$BACKEND/app/services/http_admission.py" \
  "$BACKEND/app/services/bms_pcs_control.py" \
  "$BACKEND/tests/test_http_admission.py" \
  "$BACKEND/tests/test_bms_pcs_control.py"

restore_files() {
  tar -xzf "$BACKUP" -C /
}
trap restore_files ERR

# The active Uvicorn process continues using its already-imported modules while
# the payload is verified. The actual switchover happens only at restart below.
cp -a "$PKG/payload/opt/kinetics-gateway/backend/." "$BACKEND/."
cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m compileall -q app
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_http_admission.py tests/test_api.py \
  tests/test_bms_pcs_control.py tests/test_phase5_5_control_cache.py \
  tests/test_scheduler_phase1_contract.py

if systemctl is-active --quiet bess-seven-day-v302.service; then
  SCHEDULER_WAS_ACTIVE=yes
  systemctl stop bess-seven-day-v302.service
fi

systemctl restart kinetics-gateway.service
READY=no
for _ in $(seq 1 300); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/tmp/phase5_7_1_health.json; then
    if /opt/kinetics-gateway/venv/bin/python -c \
      'import json; d=json.load(open("/tmp/phase5_7_1_health.json")); raise SystemExit(0 if (d.get("startup") or {}).get("ready") is True else 1)'; then
      READY=yes
      break
    fi
  fi
  sleep 2
done
[[ "$READY" == yes ]]

[[ "$SCHEDULER_WAS_ACTIVE" == no ]] || systemctl restart bess-seven-day-v302.service
trap - ERR
echo "Phase 5.7.1 cumulative control-truth and safe-stop-progress patch installed successfully."
echo "Rollback archive: $BACKUP"

