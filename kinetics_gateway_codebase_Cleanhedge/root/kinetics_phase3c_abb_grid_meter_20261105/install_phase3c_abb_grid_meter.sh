#!/bin/bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKUP="/root/kinetics_phase3c_abb_grid_backup_$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
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

echo "Checking exact post-Phase-3B Nagpur baseline..."
sha256sum -c "$ROOT_DIR/BASELINE_SHA256SUMS"
(
  cd "$ROOT_DIR"
  sha256sum -c PACKAGE_SHA256SUMS
)

echo "Creating rollback archive: $BACKUP"
tar -czf "$BACKUP" \
  /opt/kinetics-gateway/backend/app/core/config.py \
  /opt/kinetics-gateway/backend/app/assets/solar_driver.py \
  "$CONFIG"

rollback() {
  echo "Phase 3C installation failed; restoring pre-patch files..."
  systemctl stop "$GATEWAY" || true
  tar -xzf "$BACKUP" -C /
  rm -f /opt/kinetics-gateway/backend/tests/test_solar_grid_meter_phase3c.py
  systemctl daemon-reload
  systemctl restart "$GATEWAY" || true
}
trap rollback ERR

systemctl stop "$GATEWAY"
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/." /opt/kinetics-gateway/backend/

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
from pathlib import Path

path = Path('/etc/kinetics-gateway/config.json')
config = json.loads(path.read_text())
solar = config.setdefault('solar', {})

# Keep Hopewind as the solar-generation source.
solar.update({
    'enabled': True,
    'host': '172.16.0.111',
    'source_ip': '172.16.0.117',
    'port': 502,
    'unit_id': 1,
    'timeout_seconds': 3.0,
    'poll_seconds': 5.0,
    'stale_after_seconds': 20.0,
    'word_order': 'low_high',
    # Installation never enables charging/discharging automatically.
    'dispatch_validation_complete': False,
})

# ABB ACDB MFM becomes the authoritative grid import/export source.
solar['grid_meter'] = {
    'enabled': True,
    'host': '172.16.0.113',
    'source_ip': '172.16.0.117',
    'port': 502,
    'unit_id': 1,
    'timeout_seconds': 5.0,
    'word_order': 'low_high',
    'positive_power_is_export': True,
    'minimum_export_for_dispatch_kw': 20.0,
}

path.write_text(json.dumps(config, indent=2) + '\n')
assert config['network']['pc_api_cidr'] == '172.16.0.117/24'
PY

cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m compileall -q app
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_solar_grid_meter_phase3c.py \
  tests/test_solar_phase3b.py \
  tests/test_command_reliability_phase3.py \
  tests/test_io_arbiter_phase2.py \
  tests/test_scheduler_phase1_contract.py

systemctl daemon-reload
systemctl restart "$GATEWAY"

ready=0
for _ in $(seq 1 36); do
  if curl -fsS --connect-timeout 2 --max-time 5 \
      http://127.0.0.1:8000/api/health >/tmp/phase3c_health.json 2>/dev/null; then
    ready=1
    break
  fi
  sleep 5
done
[ "$ready" -eq 1 ]
sleep 12

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
import urllib.request

health = json.load(
    urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=10)
)
assert health['status'] == 'ok', health
assert health['assets_online'] == 20 and health['assets_total'] == 20, health

solar = health['io_arbiter']['solar']
assert solar['shared_tcp']['timeouts'] == 0, solar
assert solar['grid_meter']['completed'] > 0, solar
assert solar['grid_meter']['timeouts'] == 0, solar

print('Phase 3C health verified: Hopewind + ABB online, ABB timeouts=0')
PY

trap - ERR
echo "Phase 3C ABB grid-meter telemetry patch installed successfully."
echo "Hopewind is the solar source; ABB is the grid import/export source."
echo "Charging/discharging remains disabled: dispatch_validation_complete=false"
echo "Rollback archive: $BACKUP"
echo "Scheduler restored to previous active state: no"
