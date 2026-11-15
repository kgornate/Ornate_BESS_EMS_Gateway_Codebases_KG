#!/bin/bash
set -euo pipefail

BACKEND=/opt/kinetics-gateway/backend
echo "===== SERVICES ====="
systemctl is-active kinetics-gateway.service
echo "Scheduler (expected inactive during read-only validation):"
systemctl is-active bess-seven-day-v302.service || true

echo "===== HEALTH ====="
curl -fsS --max-time 15 http://127.0.0.1:8000/api/health \
| /opt/kinetics-gateway/venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print("status:",d.get("status")); print("ready:",(d.get("startup") or {}).get("ready")); print("assets:",d.get("assets_online"),"/",d.get("assets_total")); print("last_poll_error:",d.get("last_poll_error"))'

echo "===== READ-ONLY PERFORMANCE CONTRACTS ====="
cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_scheduler_storage.py::test_control_overview_does_not_wait_for_main_gateway_cache_lock \
  tests/test_scheduler_storage.py::test_sqlite_restart_uses_persisted_counters_without_table_scan \
  tests/test_api.py::test_scheduler_control_view_and_authoritative_compact_refresh

echo "===== CACHE-ONLY ENDPOINT LATENCY ====="
TOKEN=$(curl -fsS --max-time 10 http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"internal","password":"Internal@123"}' \
  | /opt/kinetics-gateway/venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
for _ in 1 2 3 4 5; do
  curl -fsS --max-time 5 -o /dev/null \
    -w 'control-view HTTP %{http_code} %{time_total}s\n' \
    -H "Authorization: Bearer $TOKEN" \
    http://127.0.0.1:8000/api/control-sequence/control-view
done

echo "PHASE 4 FLUTTER CONTROL-PERFORMANCE VERIFICATION PASSED"
