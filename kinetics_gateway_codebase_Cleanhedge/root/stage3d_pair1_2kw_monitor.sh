#!/bin/sh

BASE="http://127.0.0.1:8000"
PAIR="pair_1"
END=$(( $(date +%s) + 900 ))

TOKEN=$(curl -sS \
--connect-timeout 5 \
--max-time 30 \
-X POST \
"$BASE/api/auth/login" \
-H "Content-Type: application/json" \
-d '{"username":"internal","password":"Internal@123"}' |
python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

while [ "$(date +%s)" -lt "$END" ]; do
    TS=$(date -Is)
    PID=$(systemctl show kinetics-gateway.service -p MainPID --value)
    MEMORY=$(systemctl show kinetics-gateway.service -p MemoryCurrent --value)
    RESTARTS=$(systemctl show kinetics-gateway.service -p NRestarts --value)
    RSS=$(ps -p "$PID" -o rss= 2>/dev/null | tr -d ' ')
    RSS=${RSS:-0}

    curl -sS \
    --connect-timeout 3 \
    --max-time 10 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE/api/control-sequence/$PAIR/status?fresh=false" \
    -o /tmp/pair1_2kw_runtime.json

    curl -sS \
    --connect-timeout 3 \
    --max-time 10 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE/api/diagnostics/runtime-monitor/$PAIR/sample" \
    -o /tmp/pair1_2kw_sample.json

    python3 - \
    "$TS" "$MEMORY" "$RSS" "$RESTARTS" <<'PY'
import json
import sys

ts, memory, rss, restarts = sys.argv[1:]

def load(path):
    try:
        return json.load(open(path))
    except Exception as error:
        return {"parse_error": str(error)}

runtime = load("/tmp/pair1_2kw_runtime.json")
sample = load("/tmp/pair1_2kw_sample.json")

ages = sample.get("freshness_ages", {}) or {}
age_values = [
    float(value)
    for value in ages.values()
    if isinstance(value, (int, float))
]
max_age = max(age_values) if age_values else None

print(
    f"{ts} "
    f"run={runtime.get('run_status')} "
    f"stage={runtime.get('stage')} "
    f"cmd={runtime.get('commanded_power_kw')}kW "
    f"pcs_state={sample.get('pcs_operating_state')} "
    f"setpoint={sample.get('pcs_power_setpoint_kw')}kW "
    f"actual={sample.get('pcs_actual_power_kw')}kW "
    f"source={sample.get('source')} "
    f"stale={sample.get('stale')} "
    f"max_age={max_age}s "
    f"errors={sample.get('errors')} "
    f"memory={int(memory)/1048576:.1f}MB "
    f"rss={int(rss)/1024:.1f}MB "
    f"restarts={restarts} "
    f"last_error={runtime.get('last_error')}"
)
PY

    sleep 5
done
