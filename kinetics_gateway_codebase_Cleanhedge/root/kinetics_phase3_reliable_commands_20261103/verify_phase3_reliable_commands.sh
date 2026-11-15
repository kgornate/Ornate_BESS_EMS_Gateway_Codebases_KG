#!/bin/sh
set -eu

echo "===== SERVICES ====="
systemctl is-active kinetics-gateway.service
echo "Scheduler (expected inactive during read-only validation):"
systemctl is-active bess-seven-day-v302.service || true

echo "===== HEALTH ====="
curl -fsS --max-time 20 http://127.0.0.1:8000/api/health >/tmp/phase3_health.json
/opt/kinetics-gateway/venv/bin/python - <<'PY'
import json
data = json.load(open('/tmp/phase3_health.json', encoding='utf-8'))
print('status:', data.get('status'))
print('assets:', data.get('assets_online'), '/', data.get('assets_total'))
print('last_poll_error:', data.get('last_poll_error'))
print('arbiter_timeouts:', sum(
    int(lane.get('timeouts', 0))
    for group in (data.get('io_arbiter') or {}).values()
    for lane in group.values()
))
PY

echo "===== READ-ONLY RELIABILITY TESTS ====="
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
    tests/test_command_reliability_phase3.py \
    tests/test_scheduler_phase1_contract.py

echo "PHASE 3 READ-ONLY VERIFICATION PASSED"
