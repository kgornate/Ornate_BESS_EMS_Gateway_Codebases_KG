#!/bin/bash
set -euo pipefail

BACKEND=/opt/kinetics-gateway/backend
SERVICE=kinetics-gateway.service

systemctl is-active --quiet "$SERVICE"
grep -q 'control_stage_feedback_cache' "$BACKEND/app/services/bms_pcs_control.py"
grep -q 'power_telemetry_mismatch' "$BACKEND/app/services/bms_pcs_control.py"
grep -q 'required_consecutive_samples' "$BACKEND/app/services/bms_pcs_control.py"
grep -q 'run_status="aborting"' "$BACKEND/app/services/bms_pcs_control.py"

curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:8000/api/health \
  | /opt/kinetics-gateway/venv/bin/python -c '
import json,sys
d=json.load(sys.stdin)
assert d.get("status") in {"ok","degraded"}, d
print("health:", d.get("status"), "ready:", (d.get("startup") or {}).get("ready"), "assets:", d.get("assets_online"), "/", d.get("assets_total"))
'

echo "Phase 5 v2 read-only verification passed."
