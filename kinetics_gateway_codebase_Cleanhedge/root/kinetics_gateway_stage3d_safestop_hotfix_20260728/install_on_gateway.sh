#!/bin/sh
set -eu

BACKEND=/opt/kinetics-gateway/backend
SERVICE=kinetics-gateway.service
PY=/opt/kinetics-gateway/venv/bin/python3
BASE=http://127.0.0.1:8000
PKG_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_stage3d_safestop_backup_${STAMP}
EXPECTED_STAGE3C=c8c1fb8bb55ac8f09909e2f523610b8022176befd803853205f962f7d065ee02
PATCHED_HASH=643108dd3b19732b5cd061b2c006f10eaecc21541f4acd7ee4be5680b352a815

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || fail "Run as root"
[ -x "$PY" ] || fail "Gateway Python virtual environment not found"
[ -f "$BACKEND/app/services/bms_pcs_control.py" ] || fail "Missing deployed control service"
[ -f "$PKG_DIR/files/app/services/bms_pcs_control.py" ] || fail "Missing patched control service"

CURRENT_HASH=$(sha256sum "$BACKEND/app/services/bms_pcs_control.py" | awk '{print $1}')
PACKAGE_HASH=$(sha256sum "$PKG_DIR/files/app/services/bms_pcs_control.py" | awk '{print $1}')
[ "$PACKAGE_HASH" = "$PATCHED_HASH" ] || fail "Package checksum mismatch"

if [ "$CURRENT_HASH" = "$PATCHED_HASH" ]; then
    echo "Safe-stop race hotfix is already installed."
    exit 0
fi
[ "$CURRENT_HASH" = "$EXPECTED_STAGE3C" ] || fail "Deployed control file is not the verified Stage 3C baseline. Current SHA256=$CURRENT_HASH"

grep -q 'background_poll_cache_with_live_fallback' "$BACKEND/app/services/bms_pcs_control.py" \
    || fail "Stage 3C runtime monitor was not detected"
grep -q 'normal_status_default.*cached_runtime' "$BACKEND/app/services/bms_pcs_control.py" \
    || fail "Stage 3B cached status mode was not detected"

systemctl is-active --quiet "$SERVICE" || fail "$SERVICE must be active for the pre-install safe-state check"

echo "Pre-install live safe-state verification..."
TOKEN=$(curl -fsS --connect-timeout 3 --max-time 30 \
    -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"internal","password":"Internal@123"}' \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

for PAIR in pair_1 pair_2 pair_3 pair_4; do
    OUT="/tmp/safestop_patch_preflight_${PAIR}.json"
    CODE=$(curl -sS --connect-timeout 3 --max-time 180 \
        -o "$OUT" -w '%{http_code}' \
        -H "Authorization: Bearer $TOKEN" \
        "$BASE/api/control-sequence/${PAIR}/status?fresh=true")
    [ "$CODE" = "200" ] || fail "Preflight live status failed for $PAIR HTTP=$CODE"
    "$PY" - "$PAIR" "$OUT" <<'PY'
import json,sys
pair,path=sys.argv[1:]
data=json.load(open(path))
s=data.get("summary", {})
errors=data.get("errors") or []
safe=(
    not errors
    and int(s.get("precharge_state") or 0) == 0
    and s.get("positive_contactor_closed") is False
    and s.get("negative_contactor_closed") is False
    and int(s.get("pcs_operating_state") or 0) == 1
    and abs(float(s.get("pcs_power_setpoint_kw") or 0.0)) <= 0.11
    and abs(float(s.get("pcs_actual_power_kw") or 0.0)) <= 1.0
)
print({
    "pair": pair,
    "safe": safe,
    "precharge_state": s.get("precharge_state"),
    "positive_contactor_closed": s.get("positive_contactor_closed"),
    "negative_contactor_closed": s.get("negative_contactor_closed"),
    "pcs_operating_state": s.get("pcs_operating_state"),
    "pcs_power_setpoint_kw": s.get("pcs_power_setpoint_kw"),
    "pcs_actual_power_kw": s.get("pcs_actual_power_kw"),
    "errors": errors,
})
if not safe:
    raise SystemExit(f"{pair} is not at the verified safe baseline; installation aborted")
PY
    sleep 2
done

mkdir -p "$BACKUP/app/services"
cp -a "$BACKEND/app/services/bms_pcs_control.py" "$BACKUP/app/services/bms_pcs_control.py"
systemctl show "$SERVICE" > "$BACKUP/service-before.txt" 2>/dev/null || true

rollback() {
    echo
    echo "Rolling back safe-stop race hotfix..."
    systemctl stop "$SERVICE" 2>/dev/null || true
    cp -a "$BACKUP/app/services/bms_pcs_control.py" "$BACKEND/app/services/bms_pcs_control.py"
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

cd "$BACKEND"
"$PY" -m py_compile app/services/bms_pcs_control.py
PYTHONPATH="$BACKEND" "$PY" - <<'PY'
from app.services.bms_pcs_control import BmsPcsControlService
print("Safe-stop race hotfix import: OK")
PY
KINETICS_CONFIG=/etc/kinetics-gateway/config.json PYTHONPATH="$BACKEND" \
    "$PY" "$PKG_DIR/tests/run_safestop_race_checks.py"

echo "Starting $SERVICE..."
systemctl start "$SERVICE"

healthy=0
for _ in $(seq 1 40); do
    if curl -fsS --connect-timeout 2 --max-time 5 "$BASE/api/health" >/tmp/safestop_patch_health.json 2>/dev/null; then
        healthy=1
        break
    fi
    sleep 2
done
[ "$healthy" -eq 1 ] || fail "Gateway health check failed after installation"

TOKEN=$(curl -fsS --connect-timeout 3 --max-time 30 \
    -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"internal","password":"Internal@123"}' \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

CODE=$(curl -sS --connect-timeout 3 --max-time 15 \
    -o /tmp/safestop_patch_runtime_monitor.json -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE/api/diagnostics/runtime-monitor")
[ "$CODE" = "200" ] || fail "Runtime-monitor diagnostics failed HTTP=$CODE"
"$PY" - <<'PY'
import json
p='/tmp/safestop_patch_runtime_monitor.json'
data=json.load(open(p))
print({
    'mode': data.get('mode'),
    'safe_stop_monitor_quiesce': data.get('safe_stop_monitor_quiesce'),
    'active_threads': data.get('active_threads'),
})
if data.get('safe_stop_monitor_quiesce') is not True:
    raise SystemExit('Safe-stop monitor-quiesce diagnostic flag missing')
PY

INSTALLED_HASH=$(sha256sum "$BACKEND/app/services/bms_pcs_control.py" | awk '{print $1}')
[ "$INSTALLED_HASH" = "$PATCHED_HASH" ] || fail "Installed file checksum mismatch"

INSTALL_STARTED=0
trap - EXIT HUP INT TERM

echo
echo "Safe-stop race hotfix installed successfully."
echo "Backup: $BACKUP"
echo "Installed SHA256: $INSTALLED_HASH"
echo "Rollback command:"
echo "  systemctl stop $SERVICE && cp -a $BACKUP/app/services/bms_pcs_control.py $BACKEND/app/services/bms_pcs_control.py && systemctl start $SERVICE"
