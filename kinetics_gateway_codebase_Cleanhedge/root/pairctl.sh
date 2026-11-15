#!/bin/sh
set -eu

ACTION="${1:-}"
PAIR="${2:-}"
POWER="${3:-0}"

case "$PAIR" in
    1) PORT=503 ;;
    3) PORT=505 ;;
    4) PORT=506 ;;
    *)
        echo "Supported pairs: 1, 3, 4"
        exit 2
        ;;
esac

BASE="http://127.0.0.1:8000"
PY="/opt/kinetics-gateway/venv/bin/python"
CLI="/opt/kinetics-gateway/backend/tools/control_sequence_cli.py"
BAU="/tmp/bau_fc06_port.py"

STATUS="/tmp/pair${PAIR}_pairctl_status.json"

login()
{
    TOKEN=$(curl -sS \
        --connect-timeout 5 \
        --max-time 60 \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"username":"internal","password":"Internal@123"}' \
        "$BASE/api/auth/login" |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

    [ -n "$TOKEN" ] || {
        echo "ERROR: Could not generate token"
        exit 1
    }

    export TOKEN
}

get_status()
{
    curl -sS \
        --connect-timeout 5 \
        --max-time 180 \
        -H "Authorization: Bearer $TOKEN" \
        "$BASE/api/control-sequence/pair_${PAIR}/status?fresh=true" \
        -o "$STATUS"
}

print_status()
{
    python3 - "$STATUS" "$PAIR" <<'PY'
import json
import sys

path = sys.argv[1]
pair = sys.argv[2]

d = json.load(open(path))
s = d.get("summary", {})
b = s.get("blockers", {})

print({
    "pair": pair,
    "precharge": s.get("precharge_state"),
    "contactors": (
        s.get("positive_contactor_closed"),
        s.get("negative_contactor_closed"),
    ),
    "rack_v": s.get("rack_voltage_v"),
    "pcs_input_v": s.get("pcs_battery_voltage_v"),
    "pcs_dc_bus_v": s.get("pcs_dc_bus_voltage_v"),
    "pcs_state": s.get("pcs_operating_state"),
    "fault": s.get("pcs_fault_shutdown"),
    "charge_limit_a": s.get("rack_charge_current_limit_a"),
    "discharge_limit_a": s.get("rack_discharge_current_limit_a"),
    "setpoint_kw": s.get("pcs_power_setpoint_kw"),
    "actual_kw": s.get("pcs_actual_power_kw"),
    "critical_fault": b.get("documented_critical_fault"),
    "system_fault": b.get("system_fault"),
    "emergency_stop": b.get("emergency_stop_fault"),
    "errors": d.get("errors", []),
})
PY
}

wait_for()
{
    MODE="$1"
    COUNT=0

    while [ "$COUNT" -lt 40 ]; do
        get_status
        print_status

        if MODE="$MODE" python3 - "$STATUS" <<'PY'
import json
import os
import sys

d = json.load(open(sys.argv[1]))
s = d.get("summary", {})
b = s.get("blockers", {})

mode = os.environ["MODE"]

precharge = int(float(s.get("precharge_state") or 0))
positive = bool(s.get("positive_contactor_closed"))
negative = bool(s.get("negative_contactor_closed"))
pcs_state = int(float(s.get("pcs_operating_state") or 0))
fault = bool(s.get("pcs_fault_shutdown"))

setpoint = float(s.get("pcs_power_setpoint_kw") or 0)
actual = float(s.get("pcs_actual_power_kw") or 0)

charge_limit = float(
    s.get("rack_charge_current_limit_a") or 0
)
discharge_limit = float(
    s.get("rack_discharge_current_limit_a") or 0
)

critical = bool(b.get("documented_critical_fault"))
system_fault = bool(b.get("system_fault"))
emergency = bool(b.get("emergency_stop_fault"))

healthy = not any([
    fault,
    critical,
    system_fault,
    emergency,
])

if mode == "bms_ready":
    ok = (
        healthy
        and precharge == 3
        and positive
        and negative
        and charge_limit > 0
        and discharge_limit > 0
    )

elif mode == "pcs_ready":
    ok = (
        healthy
        and precharge == 3
        and positive
        and negative
        and pcs_state in (32, 80)
    )

elif mode == "zero":
    ok = (
        abs(setpoint) <= 0.5
        and abs(actual) <= 1.0
    )

elif mode == "pcs_stopped":
    ok = (
        pcs_state == 1
        and abs(setpoint) <= 0.5
        and abs(actual) <= 1.0
        and not fault
    )

elif mode == "open":
    ok = (
        pcs_state == 1
        and precharge == 0
        and not positive
        and not negative
        and abs(actual) <= 1.0
    )

else:
    ok = False

raise SystemExit(0 if ok else 1)
PY
        then
            echo "PAIR $PAIR reached condition: $MODE"
            return 0
        fi

        COUNT=$((COUNT + 1))
        sleep 3
    done

    echo "ERROR: Pair $PAIR timed out waiting for $MODE"
    return 1
}

set_power()
{
    DIRECTION="$1"
    KW="$2"

    PRECHECK="/tmp/pair${PAIR}_${DIRECTION}_${KW}_precheck.json"
    COMMAND="/tmp/pair${PAIR}_${DIRECTION}_${KW}_command.json"

    curl -sS \
        --connect-timeout 5 \
        --max-time 180 \
        -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{
          \"direction\":\"$DIRECTION\",
          \"power_kw\":$KW
        }" \
        "$BASE/api/control-sequence/pair_${PAIR}/precheck" \
        -o "$PRECHECK"

    python3 -m json.tool "$PRECHECK"

    python3 - "$PRECHECK" <<'PY'
import json
import sys

d = json.load(open(sys.argv[1]))

if not d.get("ok"):
    raise SystemExit(
        "STOPPED: power precheck did not pass"
    )
PY

    curl -sS \
        --connect-timeout 5 \
        --max-time 180 \
        -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{
          \"direction\":\"$DIRECTION\",
          \"power_kw\":$KW,
          \"confirmation\":\"EXECUTE_STAGE_WRITE\"
        }" \
        "$BASE/api/control-sequence/pair_${PAIR}/set-power" \
        -o "$COMMAND"

    python3 -m json.tool "$COMMAND"
}

login

case "$ACTION" in
    status)
        get_status
        print_status
        ;;

    precharge)
        [ -f "$BAU" ] || {
            echo "ERROR: $BAU does not exist"
            exit 1
        }

        echo "Starting Pair $PAIR BAU precharge on port $PORT"
        python3 "$BAU" "$PORT" 0x3001 1
        wait_for bms_ready
        ;;

    start)
        echo "Configuring PCS $PAIR at zero power"

        "$PY" "$CLI" configure-pcs \
            --pair-id "pair_${PAIR}" \
            --confirmation EXECUTE_STAGE_WRITE

        echo "Starting PCS $PAIR"

        "$PY" "$CLI" start-pcs \
            --pair-id "pair_${PAIR}" \
            --confirmation EXECUTE_STAGE_WRITE \
            > "/tmp/pair${PAIR}_start_pcs.txt" 2>&1 || true

        cat "/tmp/pair${PAIR}_start_pcs.txt"

        wait_for pcs_ready
        ;;

    charge|discharge)
        set_power "$ACTION" "$POWER"
        sleep 5
        get_status
        print_status
        ;;

    zero)
        echo "Commanding Pair $PAIR to zero power"
        set_power charge 0
        wait_for zero
        ;;

    stop)
        echo "Step 1: Bring Pair $PAIR to zero power"
        set_power charge 0
        wait_for zero

        echo "Step 2: Stop PCS $PAIR"

        "$PY" "$CLI" safe-stop \
            --pair-id "pair_${PAIR}" \
            --confirmation EXECUTE_STAGE_WRITE \
            > "/tmp/pair${PAIR}_safe_stop.txt" 2>&1 || true

        cat "/tmp/pair${PAIR}_safe_stop.txt"

        wait_for pcs_stopped

        echo "Step 3: Open Pair $PAIR BMS contactors through BAU port $PORT"
        python3 "$BAU" "$PORT" 0x3001 2

        wait_for open
        ;;

    run-charge)
        "$0" precharge "$PAIR"
        "$0" start "$PAIR"
        "$0" charge "$PAIR" "$POWER"
        ;;

    run-discharge)
        "$0" precharge "$PAIR"
        "$0" start "$PAIR"
        "$0" discharge "$PAIR" "$POWER"
        ;;

    *)
        echo "Usage:"
        echo "  $0 status PAIR"
        echo "  $0 precharge PAIR"
        echo "  $0 start PAIR"
        echo "  $0 charge PAIR KW"
        echo "  $0 discharge PAIR KW"
        echo "  $0 zero PAIR"
        echo "  $0 stop PAIR"
        echo "  $0 run-charge PAIR KW"
        echo "  $0 run-discharge PAIR KW"
        exit 2
        ;;
esac
