#!/bin/bash
set -euo pipefail

echo "===== SERVICES ====="
systemctl is-active kinetics-gateway.service
echo "Scheduler (expected inactive):"
systemctl is-active bess-seven-day-v302.service || true

echo "===== HEALTH / MODBUS LINKS ====="
curl -fsS --max-time 15 http://127.0.0.1:8000/api/health \
  >/tmp/phase3c_verify_health.json

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json

health = json.load(open('/tmp/phase3c_verify_health.json'))
solar = health['io_arbiter']['solar']

print('status:', health['status'])
print('assets:', health['assets_online'], '/', health['assets_total'])
print('Hopewind completed:', solar['shared_tcp']['completed'])
print('Hopewind timeouts:', solar['shared_tcp']['timeouts'])
print('ABB completed:', solar['grid_meter']['completed'])
print('ABB timeouts:', solar['grid_meter']['timeouts'])

assert health['status'] == 'ok'
assert health['assets_online'] == 20
assert health['assets_total'] == 20
assert solar['grid_meter']['completed'] > 0
assert solar['grid_meter']['timeouts'] == 0
PY

echo "===== READ-ONLY INTEGRATION TESTS ====="
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_solar_grid_meter_phase3c.py \
  tests/test_solar_phase3b.py

echo "PHASE 3C ABB GRID-METER VERIFICATION PASSED"
