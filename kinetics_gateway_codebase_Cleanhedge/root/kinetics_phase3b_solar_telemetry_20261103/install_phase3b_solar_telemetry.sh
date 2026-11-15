#!/bin/bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKUP="/root/kinetics_phase3b_solar_backup_$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
CONFIG=/etc/kinetics-gateway/config.json
SCHEDULER=bess-seven-day-v302.service
GATEWAY=kinetics-gateway.service

if [ "${CONFIRM_IDLE:-}" != "YES" ]; then
  echo "Refusing installation: run with CONFIRM_IDLE=YES"
  exit 2
fi
if [ "$(systemctl is-active "$SCHEDULER" || true)" = "active" ]; then
  echo "Refusing installation: scheduler must remain inactive"
  exit 3
fi

echo "Checking exact post-Phase-3 Nagpur baseline..."
sha256sum -c "$ROOT_DIR/BASELINE_SHA256SUMS"
sha256sum -c "$ROOT_DIR/PACKAGE_SHA256SUMS"

echo "Creating rollback archive: $BACKUP"
tar -czf "$BACKUP" \
  /opt/kinetics-gateway/backend/app/core/config.py \
  /opt/kinetics-gateway/backend/app/main.py \
  /opt/kinetics-gateway/backend/app/services/gateway_service.py \
  "$CONFIG"

rollback() {
  echo "Phase 3B installation failed; restoring pre-patch files..."
  systemctl stop "$GATEWAY" || true
  tar -xzf "$BACKUP" -C /
  rm -f /opt/kinetics-gateway/backend/app/assets/solar_driver.py \
        /opt/kinetics-gateway/backend/tests/test_solar_phase3b.py
  systemctl daemon-reload
  systemctl restart "$GATEWAY" || true
}
trap rollback ERR

systemctl stop "$GATEWAY"
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/." /opt/kinetics-gateway/backend/

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
from pathlib import Path
p = Path('/etc/kinetics-gateway/config.json')
c = json.loads(p.read_text())
c['solar'] = {
    'enabled': True,
    'host': '172.16.0.111',
    'source_ip': '172.16.0.117',
    'port': 502,
    'unit_id': 1,
    'timeout_seconds': 3.0,
    'poll_seconds': 5.0,
    'stale_after_seconds': 20.0,
    'word_order': 'low_high',
    'dispatch_validation_complete': False,
}
p.write_text(json.dumps(c, indent=2) + '\n')
assert c['network']['pc_api_cidr'] == '172.16.0.117/24'
PY

cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m compileall -q app
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_solar_phase3b.py \
  tests/test_command_reliability_phase3.py \
  tests/test_io_arbiter_phase2.py \
  tests/test_scheduler_phase1_contract.py

systemctl daemon-reload
systemctl restart "$GATEWAY"
ready=0
for _ in $(seq 1 36); do
  if curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health >/tmp/phase3b_health.json 2>/dev/null; then
    ready=1
    break
  fi
  sleep 5
done
[ "$ready" -eq 1 ]
sleep 8

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json, urllib.request
h = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=10))
assert h['status'] == 'ok', h
assert h['assets_online'] == 20 and h['assets_total'] == 20, h
solar = h['io_arbiter']['solar']['shared_tcp']
assert solar['timeouts'] == 0, solar
print('Phase 3B health verified: 20/20 assets, solar arbiter timeouts=0')
PY

trap - ERR
echo "Phase 3B read-only solar telemetry patch installed successfully."
echo "Solar dispatch remains gated: dispatch_validation_complete=false"
echo "Rollback archive: $BACKUP"
echo "Scheduler restored to previous active state: no"
