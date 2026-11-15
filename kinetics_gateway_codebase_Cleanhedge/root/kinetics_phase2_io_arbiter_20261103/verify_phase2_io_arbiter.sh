#!/bin/sh
set -eu

echo "===== SERVICES ====="
systemctl is-active kinetics-gateway.service
echo "Scheduler (expected inactive during read-only validation):"
systemctl is-active bess-seven-day-v302.service || true

echo "===== HEALTH AND ARBITER ====="
curl -fsS --max-time 20 http://127.0.0.1:8000/api/health >/tmp/phase2_health.json
/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
data = json.load(open('/tmp/phase2_health.json', encoding='utf-8'))
print('status:', data.get('status'))
print('assets:', data.get('assets_online'), '/', data.get('assets_total'))
print('last_poll_error:', data.get('last_poll_error'))
print(json.dumps(data.get('io_arbiter'), indent=2))
PY

echo "===== READ-ONLY PRIORITY TESTS ====="
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
    tests/test_io_arbiter_phase2.py

echo "PHASE 2 READ-ONLY VERIFICATION PASSED"
