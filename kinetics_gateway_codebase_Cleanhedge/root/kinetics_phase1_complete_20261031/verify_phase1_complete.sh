#!/bin/bash
set -Eeuo pipefail

TOKEN=${TOKEN:-}
if [ -z "$TOKEN" ]; then
  echo "Set the internal bearer token first: TOKEN='...' bash verify_phase1_complete.sh"
  exit 2
fi

AUTH="Authorization: Bearer $TOKEN"
curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:8000/api/health | python3 -m json.tool
curl -fsS --connect-timeout 3 --max-time 10 -H "$AUTH" http://127.0.0.1:8000/api/control-sequence/control-view | python3 -m json.tool
curl -fsS --connect-timeout 3 --max-time 40 -H "$AUTH" 'http://127.0.0.1:8000/api/control-sequence/pair_1/status/compact?fresh=true&wait_seconds=15' | python3 -m json.tool
curl -fsS --connect-timeout 3 --max-time 10 -H "$AUTH" http://127.0.0.1:8000/api/diagnostics/runtime | python3 -m json.tool
systemctl status kinetics-gateway.service --no-pager -l
systemctl status bess-seven-day-v302.service --no-pager -l || true
echo "Phase 1 verification requests completed."
