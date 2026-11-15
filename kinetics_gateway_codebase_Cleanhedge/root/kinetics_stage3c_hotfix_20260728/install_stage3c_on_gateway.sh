#!/bin/sh
set -eu

BACKEND=/opt/kinetics-gateway/backend
SERVICE=kinetics-gateway.service
PY=/opt/kinetics-gateway/venv/bin/python3
BASE=http://127.0.0.1:8000
PKG_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_stage3c_backup_${STAMP}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || fail "Run as root"
[ -x "$PY" ] || fail "Gateway Python virtual environment not found"
[ -f "$BACKEND/app/services/bms_pcs_control.py" ] || fail "Missing deployed control service"
[ -f "$BACKEND/app/services/gateway_service.py" ] || fail "Missing deployed gateway service"
[ -f "$BACKEND/app/api/routes.py" ] || fail "Missing deployed routes"
[ -f "$PKG_DIR/files/app/services/bms_pcs_control.py" ] || fail "Missing Stage 3C control service"
[ -f "$PKG_DIR/files/app/services/gateway_service.py" ] || fail "Missing Stage 3C gateway service"
[ -f "$PKG_DIR/files/app/api/routes.py" ] || fail "Missing Stage 3C routes"

# Stage 3C must sit on top of the verified Stage 3A + 3B deployment.
grep -q '_storage_status_cache' "$BACKEND/app/services/gateway_service.py" \
    || fail "Stage 3A was not detected"
grep -q 'normal_status_default.*cached_runtime' "$BACKEND/app/services/bms_pcs_control.py" \
    || fail "Stage 3B was not detected"
grep -q 'global_live_refresh_limit' "$BACKEND/app/services/bms_pcs_control.py" \
    || fail "Stage 3B single-flight protection was not detected"

systemctl is-active --quiet "$SERVICE" || fail "$SERVICE must be active for the pre-install safe-state check"

echo "Pre-install live safe-state verification..."
TOKEN=$(curl -fsS --connect-timeout 3 --max-time 30 \
    -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"internal","password":"Internal@123"}' \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

for PAIR in pair_1 pair_2 pair_3 pair_4; do
    OUT="/tmp/stage3c_preflight_${PAIR}.json"
    CODE=$(curl -sS --connect-timeout 3 --max-time 180 \
        -o "$OUT" -w '%{http_code}' \
        -H "Authorization: Bearer $TOKEN" \
        "$BASE/api/control-sequence/${PAIR}/status?fresh=true")
    [ "$CODE" = "200" ] || fail "Preflight live status failed for $PAIR HTTP=$CODE"
    "$PY" - "$PAIR" "$OUT" <<'PY'
import json, math, sys
pair, path = sys.argv[1:]
data = json.load(open(path))
s = data.get("summary", {})
errors = data.get("errors") or []
pre = s.get("precharge_state")
pos = s.get("positive_contactor_closed")
neg = s.get("negative_contactor_closed")
state = s.get("pcs_operating_state")
setpoint = s.get("pcs_power_setpoint_kw")
actual = s.get("pcs_actual_power_kw")
safe = (
    not errors
    and pre is not None and int(pre) == 0
    and pos is False
    and neg is False
    and state is not None and int(state) == 1
    and setpoint is not None and abs(float(setpoint)) <= 0.11
    and actual is not None and abs(float(actual)) <= 1.0
)
print({
    "pair": pair,
    "safe": safe,
    "precharge_state": pre,
    "positive_contactor_closed": pos,
    "negative_contactor_closed": neg,
    "pcs_operating_state": state,
    "pcs_power_setpoint_kw": setpoint,
    "pcs_actual_power_kw": actual,
    "errors": errors,
})
if not safe:
    raise SystemExit(f"{pair} is not at the verified safe baseline; Stage 3C installation aborted")
PY
    sleep 2
done

mkdir -p "$BACKUP/app/services" "$BACKUP/app/api"
cp -a "$BACKEND/app/services/bms_pcs_control.py" "$BACKUP/app/services/bms_pcs_control.py"
cp -a "$BACKEND/app/services/gateway_service.py" "$BACKUP/app/services/gateway_service.py"
cp -a "$BACKEND/app/api/routes.py" "$BACKUP/app/api/routes.py"
systemctl show "$SERVICE" > "$BACKUP/service-before.txt" 2>/dev/null || true

rollback() {
    echo
    echo "Rolling back Stage 3C..."
    systemctl stop "$SERVICE" 2>/dev/null || true
    cp -a "$BACKUP/app/services/bms_pcs_control.py" "$BACKEND/app/services/bms_pcs_control.py"
    cp -a "$BACKUP/app/services/gateway_service.py" "$BACKEND/app/services/gateway_service.py"
    cp -a "$BACKUP/app/api/routes.py" "$BACKEND/app/api/routes.py"
    systemctl start "$SERVICE" 2>/dev/null || true
    echo "Rollback attempted from: $BACKUP"
}

INSTALL_STARTED=0
install_exit() {
    code=$?
    trap - EXIT HUP INT TERM
    if [ "$code" -ne 0 ] && [ "$INSTALL_STARTED" -eq 1 ]; then
        rollback
    fi
    exit "$code"
}
trap install_exit EXIT HUP INT TERM

echo "Stopping $SERVICE..."
INSTALL_STARTED=1
systemctl stop "$SERVICE"

install -m 0644 "$PKG_DIR/files/app/services/bms_pcs_control.py" \
    "$BACKEND/app/services/bms_pcs_control.py"
install -m 0644 "$PKG_DIR/files/app/services/gateway_service.py" \
    "$BACKEND/app/services/gateway_service.py"
install -m 0644 "$PKG_DIR/files/app/api/routes.py" \
    "$BACKEND/app/api/routes.py"

cd "$BACKEND"
"$PY" -m py_compile \
    app/services/bms_pcs_control.py \
    app/services/gateway_service.py \
    app/api/routes.py
PYTHONPATH="$BACKEND" "$PY" - <<'PY'
from app.api.routes import build_router
from app.services.bms_pcs_control import BmsPcsControlService
from app.services.gateway_service import GatewayService
print("Stage 3C imports: OK")
PY
KINETICS_CONFIG=/etc/kinetics-gateway/config.json PYTHONPATH="$BACKEND" "$PY" "$PKG_DIR/tests/run_stage3c_unit_checks.py"

echo "Starting $SERVICE..."
systemctl start "$SERVICE"

healthy=0
for _ in $(seq 1 40); do
    if curl -fsS --connect-timeout 2 --max-time 5 \
        "$BASE/api/health" >/tmp/stage3c_health.json 2>/dev/null; then
        healthy=1
        break
    fi
    sleep 2
done
[ "$healthy" -eq 1 ] || fail "Gateway health check failed after Stage 3C installation"

TOKEN=$(curl -fsS --connect-timeout 3 --max-time 30 \
    -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"internal","password":"Internal@123"}' \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

for PAIR in pair_1 pair_2 pair_3 pair_4; do
    OUT="/tmp/stage3c_${PAIR}_cache_sample.json"
    CODE=$(curl -sS --connect-timeout 3 --max-time 15 \
        -o "$OUT" -w '%{http_code}' \
        -H "Authorization: Bearer $TOKEN" \
        "$BASE/api/diagnostics/runtime-monitor/${PAIR}/sample")
    [ "$CODE" = "200" ] || fail "Runtime-monitor cache sample failed for $PAIR HTTP=$CODE"
    "$PY" - "$PAIR" "$OUT" <<'PY'
import json,sys
pair,path=sys.argv[1:]
data=json.load(open(path))
source=data.get("refresh",{}).get("source")
errors=data.get("errors") or []
print(pair, "source=", source, "errors=", errors)
if source != "background_poll_cache" or errors:
    raise SystemExit(f"Stage 3C cache sample validation failed for {pair}")
PY
done

CODE=$(curl -sS --connect-timeout 3 --max-time 10 \
    -o /tmp/stage3c_runtime_monitor_diagnostics.json \
    -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE/api/diagnostics/runtime-monitor")
[ "$CODE" = "200" ] || fail "Runtime-monitor diagnostics endpoint failed HTTP=$CODE"

INSTALL_STARTED=0
trap - EXIT HUP INT TERM

echo
echo "Stage 3C installation completed successfully."
echo "Backup: $BACKUP"
echo "Rollback command:"
echo "  systemctl stop $SERVICE && cp -a $BACKUP/app/services/bms_pcs_control.py $BACKEND/app/services/bms_pcs_control.py && cp -a $BACKUP/app/services/gateway_service.py $BACKEND/app/services/gateway_service.py && cp -a $BACKUP/app/api/routes.py $BACKEND/app/api/routes.py && systemctl start $SERVICE"
echo
echo "Keep Flutter closed and run:"
echo "  sh $PKG_DIR/verify_stage3c.sh"
