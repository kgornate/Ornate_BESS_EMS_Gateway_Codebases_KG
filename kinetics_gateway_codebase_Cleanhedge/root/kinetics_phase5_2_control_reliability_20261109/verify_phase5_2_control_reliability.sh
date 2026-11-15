#!/bin/bash
set -euo pipefail
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m compileall -q app
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short \
  tests/test_http_admission.py tests/test_command_reliability_phase3.py \
  tests/test_api.py::test_scheduler_control_view_and_authoritative_compact_refresh \
  tests/test_bms_pcs_control.py
curl -fsS --max-time 10 http://127.0.0.1:8000/api/health | /opt/kinetics-gateway/venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print("STATUS:",d.get("status"),"| READY:",(d.get("startup") or {}).get("ready"),"| ASSETS:",d.get("assets_online"),"/",d.get("assets_total"))'
echo "Phase 5.2 read-only verification passed."
