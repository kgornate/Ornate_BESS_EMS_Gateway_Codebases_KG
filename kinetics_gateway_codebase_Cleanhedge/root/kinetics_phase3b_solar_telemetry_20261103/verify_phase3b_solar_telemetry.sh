#!/bin/bash
set -euo pipefail
echo "===== SERVICES ====="
systemctl is-active kinetics-gateway.service
echo "Scheduler (expected inactive):"
systemctl is-active bess-seven-day-v302.service || true
echo "===== HEALTH / SOLAR ====="
curl -fsS http://127.0.0.1:8000/api/health >/tmp/phase3b_health.json
/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
h=json.load(open('/tmp/phase3b_health.json'))
print('status:',h['status'])
print('assets:',h['assets_online'],'/',h['assets_total'])
s=h['io_arbiter']['solar']['shared_tcp']
print('solar completed:',s['completed'])
print('solar timeouts:',s['timeouts'])
assert h['status']=='ok' and h['assets_online']==20 and h['assets_total']==20
assert s['timeouts']==0
PY
echo "===== READ-ONLY TESTS ====="
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_solar_phase3b.py tests/test_command_reliability_phase3.py
echo "PHASE 3B READ-ONLY VERIFICATION PASSED"
