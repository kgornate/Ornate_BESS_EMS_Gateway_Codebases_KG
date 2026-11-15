#!/usr/bin/env bash

set -u
set -o pipefail

BASE_URL="http://127.0.0.1:8000"
POWER_KW=20
RAMP_STEP_KW=10
RAMP_INTERVAL_SECONDS=5

ACTION="${1:-}"
PAIR="${2:-}"
DIRECTION="${3:-}"

case "${PAIR}" in
    pair_1|pair_2|pair_3|pair_4)
        ;;
    *)
        echo "ERROR: Pair must be pair_1, pair_2, pair_3 or pair_4."
        exit 2
        ;;
esac

TOKEN=$(
    curl -sS -X POST \
    "${BASE_URL}/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"internal","password":"Internal@123"}' |
    python3 -c '
import json
import sys
print(json.load(sys.stdin)["access_token"])
'
)

if [ -z "${TOKEN}" ]; then
    echo "ERROR: API login failed."
    exit 1
fi

export TOKEN

check_controller()
{
    curl -sS \
    --max-time 30 \
    -H "Authorization: Bearer ${TOKEN}" \
    "${BASE_URL}/api/control-sequence/capabilities" \
    -o /tmp/v251_capabilities.json

    python3 - <<'PY'
import json
import sys

d = json.load(open("/tmp/v251_capabilities.json"))

print({
    "controller": d.get("controller"),
    "version": d.get("version"),
    "automatic_allowed": d.get("full_automatic_sequence_allowed"),
})

if d.get("version") != "2.5.1":
    print("ERROR: Controller version is not 2.5.1.")
    sys.exit(1)

if not d.get("full_automatic_sequence_allowed"):
    print("ERROR: Automatic sequence is disabled.")
    sys.exit(1)
PY
}

run_pair()
{
    local pair="$1"
    local direction="$2"
    local output="/tmp/${pair}_${direction}_start.json"
    local verify="/tmp/${pair}_${direction}_verify.json"
    local http_code

    case "${direction}" in
        discharge|charge)
            ;;
        *)
            echo "ERROR: Direction must be discharge or charge."
            exit 2
            ;;
    esac

    echo
    echo "=================================================="
    echo "Starting ${pair} ${direction} test"
    echo "Target power: ${POWER_KW} kW"
    echo "=================================================="

    http_code=$(
        curl -sS \
        --max-time 60 \
        -o "${output}" \
        -w "%{http_code}" \
        -X POST \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{
          \"direction\":\"${direction}\",
          \"power_kw\":${POWER_KW},
          \"ramp_step_kw\":${RAMP_STEP_KW},
          \"ramp_interval_seconds\":${RAMP_INTERVAL_SECONDS},
          \"confirmation\":\"EXECUTE_AUTOMATIC_SEQUENCE\"
        }" \
        "${BASE_URL}/api/control-sequence/${pair}/automatic-start"
    )

    echo "HTTP=${http_code}"
    python3 -m json.tool "${output}" 2>/dev/null || cat "${output}"

    if [ "${http_code}" != "202" ]; then
        echo "ERROR: Sequence was not accepted."
        exit 1
    fi

    PAIR="${pair}" python3 - <<'PY'
import json
import os
import sys
import time
import urllib.request

pair = os.environ["PAIR"]
token = os.environ["TOKEN"]

url = (
    "http://127.0.0.1:8000"
    f"/api/control-sequence/{pair}/status?fresh=false"
)

start = time.time()

while True:
    if time.time() - start > 900:
        print("ERROR: Monitoring exceeded 15 minutes.")
        sys.exit(1)

    try:
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )

        with urllib.request.urlopen(request, timeout=60) as response:
            d = json.load(response)

        result = {
            "pair": pair,
            "run_status": d.get("run_status"),
            "stage": d.get("stage"),
            "commanded_power_kw": d.get("commanded_power_kw"),
            "last_error": d.get("last_error"),
            "steps": {
                step.get("key"): step.get("status")
                for step in d.get("steps", [])
            },
        }

        print(result, flush=True)

        status = d.get("run_status")

        if status == "success":
            break

        if status in {"failed", "aborted"}:
            print("ERROR: Automatic sequence failed or was aborted.")
            sys.exit(1)

    except Exception as error:
        print({
            "monitor_warning": str(error),
            "message": "Retrying cached status."
        }, flush=True)

    time.sleep(5)
PY

    curl -sS \
    --max-time 180 \
    -H "Authorization: Bearer ${TOKEN}" \
    "${BASE_URL}/api/control-sequence/${pair}/status?fresh=true" \
    -o "${verify}"

    PAIR="${pair}" DIRECTION="${direction}" POWER_KW="${POWER_KW}" \
    python3 - <<'PY'
import json
import os
import sys

pair = os.environ["PAIR"]
direction = os.environ["DIRECTION"]
power = float(os.environ["POWER_KW"])

expected = power if direction == "discharge" else -power

d = json.load(
    open(f"/tmp/{pair}_{direction}_verify.json")
)

s = d.get("summary", {})
r = d.get("runtime", {})
b = s.get("blockers", {})

precharge = s.get("precharge_state")
positive = s.get("positive_contactor_closed")
negative = s.get("negative_contactor_closed")
pcs_state = s.get("pcs_operating_state")
breaker = s.get("pcs_dc_breaker_feedback_closed")
setpoint = s.get("pcs_power_setpoint_kw")
actual = s.get("pcs_actual_power_kw")
critical = b.get("documented_critical_fault")
estop = b.get("emergency_stop_fault")
errors = d.get("errors", [])

result = {
    "pair": pair,
    "direction": direction,
    "run_status": r.get("run_status"),
    "stage": r.get("stage"),
    "precharge_state": precharge,
    "contactors": (positive, negative),
    "pcs_state": pcs_state,
    "pcs_input_v": s.get("pcs_battery_voltage_v"),
    "pcs_dc_bus_v": s.get("pcs_dc_bus_voltage_v"),
    "dc_breaker_closed": breaker,
    "setpoint_kw": setpoint,
    "actual_kw": actual,
    "critical_fault": critical,
    "estop": estop,
    "errors": errors,
}

print(result)

valid = (
    r.get("run_status") == "success"
    and precharge is not None
    and int(float(precharge)) == 3
    and positive is True
    and negative is True
    and pcs_state is not None
    and int(float(pcs_state)) in {16, 32, 64, 80}
    and breaker is True
    and setpoint is not None
    and abs(float(setpoint) - expected) <= 1.0
    and actual is not None
    and abs(float(actual) - expected) <= 5.0
    and critical is False
    and estop is False
    and not errors
)

if not valid:
    print("ERROR: Active-power verification failed.")
    sys.exit(1)

print(
    f"SUCCESS: {pair} {direction} verified. "
    f"Setpoint={setpoint} kW, actual={actual} kW"
)
PY
}

stop_pair()
{
    local pair="$1"
    local response="/tmp/${pair}_safe_stop.json"
    local verify="/tmp/${pair}_safe_verify.json"
    local http_code

    echo
    echo "=================================================="
    echo "Safe-stopping ${pair}"
    echo "=================================================="

    http_code=$(
        curl -sS \
        --max-time 240 \
        -o "${response}" \
        -w "%{http_code}" \
        -X POST \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "open_bms":true,
          "confirmation":"EXECUTE_STAGE_WRITE"
        }' \
        "${BASE_URL}/api/control-sequence/${pair}/safe-stop"
    )

    echo "HTTP=${http_code}"
    python3 -m json.tool "${response}" 2>/dev/null || cat "${response}"

    sleep 10

    curl -sS \
    --max-time 180 \
    -H "Authorization: Bearer ${TOKEN}" \
    "${BASE_URL}/api/control-sequence/${pair}/status?fresh=true" \
    -o "${verify}"

    PAIR="${pair}" python3 - <<'PY'
import json
import os
import sys

pair = os.environ["PAIR"]
d = json.load(open(f"/tmp/{pair}_safe_verify.json"))

s = d.get("summary", {})
b = s.get("blockers", {})

precharge = s.get("precharge_state")
positive = s.get("positive_contactor_closed")
negative = s.get("negative_contactor_closed")
pcs_state = s.get("pcs_operating_state")
setpoint = s.get("pcs_power_setpoint_kw")
actual = s.get("pcs_actual_power_kw")
critical = b.get("documented_critical_fault")
errors = d.get("errors", [])

result = {
    "pair": pair,
    "precharge_state": precharge,
    "contactors": (positive, negative),
    "pcs_state": pcs_state,
    "setpoint_kw": setpoint,
    "actual_kw": actual,
    "critical_fault": critical,
    "errors": errors,
}

print(result)

safe = (
    precharge is not None
    and int(float(precharge)) == 0
    and positive is False
    and negative is False
    and pcs_state is not None
    and int(float(pcs_state)) == 1
    and setpoint is not None
    and abs(float(setpoint)) <= 0.5
    and actual is not None
    and abs(float(actual)) <= 2.0
    and critical is False
    and not errors
)

if not safe:
    print("ERROR: Safe-state verification failed.")
    sys.exit(1)

print(f"SUCCESS: {pair} is safely stopped.")
PY
}

check_controller

case "${ACTION}" in
    run)
        run_pair "${PAIR}" "${DIRECTION}"
        ;;

    stop)
        stop_pair "${PAIR}"
        ;;

    *)
        echo "Usage:"
        echo "  bash /root/pair_manual_v251.sh run pair_1 discharge"
        echo "  bash /root/pair_manual_v251.sh stop pair_1"
        echo "  bash /root/pair_manual_v251.sh run pair_1 charge"
        exit 2
        ;;
esac
