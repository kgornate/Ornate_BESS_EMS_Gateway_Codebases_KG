#!/bin/sh
set -eu
BASE=http://127.0.0.1:8000
PY=/opt/kinetics-gateway/venv/bin/python3
SERVICE=kinetics-gateway.service
EXPECTED=643108dd3b19732b5cd061b2c006f10eaecc21541f4acd7ee4be5680b352a815

systemctl is-active --quiet "$SERVICE"
ACTUAL=$(sha256sum /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py | awk '{print $1}')
[ "$ACTUAL" = "$EXPECTED" ] || { echo "Unexpected control-service hash: $ACTUAL"; exit 1; }
TOKEN=$(curl -fsS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' -d '{"username":"internal","password":"Internal@123"}' | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/api/diagnostics/runtime-monitor" -o /tmp/safestop_patch_verify.json
"$PY" - <<'PY'
import json
x=json.load(open('/tmp/safestop_patch_verify.json'))
print('mode:', x.get('mode'))
print('safe_stop_monitor_quiesce:', x.get('safe_stop_monitor_quiesce'))
print('active_threads:', x.get('active_threads'))
assert x.get('safe_stop_monitor_quiesce') is True
PY
systemctl show "$SERVICE" -p MainPID -p NRestarts -p MemoryCurrent -p MemoryPeak
echo "Safe-stop race hotfix verification: PASS"
