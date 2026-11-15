#!/bin/sh

BASE="http://127.0.0.1:8000"

TOKEN=$(curl -sS \
--connect-timeout 5 \
--max-time 30 \
-X POST \
"$BASE/api/auth/login" \
-H "Content-Type: application/json" \
-d '{"username":"internal","password":"Internal@123"}' |
python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

while true; do
    curl -sS \
    --connect-timeout 3 \
    --max-time 15 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE/api/control-sequence/pair_1/status?fresh=false" \
    -o /tmp/pair1_runtime.json

    curl -sS \
    --connect-timeout 3 \
    --max-time 15 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE/api/diagnostics/runtime-monitor/pair_1/sample" \
    -o /tmp/pair1_sample.json

    python3 - <<'PY'
import json
import subprocess
from datetime import datetime, timezone

runtime = json.load(open("/tmp/pair1_runtime.json"))
sample = json.load(open("/tmp/pair1_sample.json"))

summary = sample.get("summary", {})
refresh = sample.get("refresh", {})

memory = subprocess.check_output(
    [
        "systemctl", "show", "kinetics-gateway.service",
        "-p", "MemoryCurrent", "--value",
    ],
    text=True,
).strip()

restarts = subprocess.check_output(
    [
        "systemctl", "show", "kinetics-gateway.service",
        "-p", "NRestarts", "--value",
    ],
    text=True,
).strip()

print(
    datetime.now(timezone.utc).isoformat(),
    f"run={runtime.get('run_status')}",
    f"stage={runtime.get('stage')}",
    f"cmd={runtime.get('commanded_power_kw')}kW",
    f"precharge={summary.get('precharge_state')}",
    f"contactors={summary.get('positive_contactor_closed')}/"
    f"{summary.get('negative_contactor_closed')}",
    f"pcs_state={summary.get('pcs_operating_state')}",
    f"setpoint={summary.get('pcs_power_setpoint_kw')}kW",
    f"actual={summary.get('pcs_actual_power_kw')}kW",
    f"bms_limit={summary.get('bms_discharge_power_limit_kw')}kW",
    f"source={refresh.get('source')}",
    f"stale={refresh.get('stale')}",
    f"errors={sample.get('errors')}",
    f"memory={int(memory)/1048576:.1f}MB",
    f"restarts={restarts}",
    f"last_error={runtime.get('last_error')}",
)
PY

    sleep 5
done
