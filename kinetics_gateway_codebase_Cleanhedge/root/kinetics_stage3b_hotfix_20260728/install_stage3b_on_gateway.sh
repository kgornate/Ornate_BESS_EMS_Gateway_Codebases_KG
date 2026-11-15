#!/bin/sh
set -eu

BACKEND=/opt/kinetics-gateway/backend
SERVICE=kinetics-gateway.service
PKG_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_stage3b_backup_${STAMP}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || fail "Run as root"
[ -f "$BACKEND/app/api/routes.py" ] || fail "Missing deployed routes.py"
[ -f "$BACKEND/app/services/bms_pcs_control.py" ] || fail "Missing deployed control service"
[ -f "$PKG_DIR/files/app/api/routes.py" ] || fail "Missing Stage 3B routes.py"
[ -f "$PKG_DIR/files/app/services/bms_pcs_control.py" ] || fail "Missing Stage 3B control service"

# Stage 3B is designed to sit on top of Stage 3A.
grep -q '_storage_status_cache' "$BACKEND/app/services/gateway_service.py" \
    || fail "Stage 3A gateway_service.py was not detected. Do not install Stage 3B on the older backend."

mkdir -p "$BACKUP/app/api" "$BACKUP/app/services"
cp -a "$BACKEND/app/api/routes.py" "$BACKUP/app/api/routes.py"
cp -a "$BACKEND/app/services/bms_pcs_control.py" "$BACKUP/app/services/bms_pcs_control.py"
systemctl show "$SERVICE" > "$BACKUP/service-before.txt" 2>/dev/null || true

rollback() {
    echo
    echo "Rolling back Stage 3B..."
    systemctl stop "$SERVICE" 2>/dev/null || true
    cp -a "$BACKUP/app/api/routes.py" "$BACKEND/app/api/routes.py"
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

install -m 0644 "$PKG_DIR/files/app/api/routes.py" \
    "$BACKEND/app/api/routes.py"
install -m 0644 "$PKG_DIR/files/app/services/bms_pcs_control.py" \
    "$BACKEND/app/services/bms_pcs_control.py"

PY=/opt/kinetics-gateway/venv/bin/python3
[ -x "$PY" ] || fail "Gateway Python virtual environment not found"

cd "$BACKEND"
"$PY" -m py_compile \
    app/api/routes.py \
    app/services/bms_pcs_control.py
PYTHONPATH="$BACKEND" "$PY" - <<'PY'
from app.api.routes import build_router
from app.services.bms_pcs_control import BmsPcsControlService, ControlStatusBusyError
print("Stage 3B imports: OK")
PY

echo "Starting $SERVICE..."
systemctl start "$SERVICE"

healthy=0
for _ in $(seq 1 30); do
    if curl -fsS --connect-timeout 2 --max-time 5 \
        http://127.0.0.1:8000/api/health >/tmp/stage3b_health.json 2>/dev/null; then
        healthy=1
        break
    fi
    sleep 2
done

if [ "$healthy" -ne 1 ]; then
    fail "Gateway health check failed after Stage 3B installation"
fi

TOKEN=$(curl -fsS --connect-timeout 3 --max-time 30 \
    -X POST http://127.0.0.1:8000/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"internal","password":"Internal@123"}' \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

for PAIR in pair_1 pair_2 pair_3 pair_4; do
    CODE=$(curl -sS --connect-timeout 3 --max-time 10 \
        -o "/tmp/stage3b_${PAIR}_cached.json" \
        -w '%{http_code}' \
        -H "Authorization: Bearer $TOKEN" \
        "http://127.0.0.1:8000/api/control-sequence/${PAIR}/status")
    [ "$CODE" = "200" ] || {
        fail "Default cached status failed for $PAIR HTTP=$CODE"
    }
done

CODE=$(curl -sS --connect-timeout 3 --max-time 10 \
    -o /tmp/stage3b_refresh_diagnostics.json \
    -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    http://127.0.0.1:8000/api/diagnostics/control-refresh)
[ "$CODE" = "200" ] || {
    fail "Control-refresh diagnostics endpoint failed HTTP=$CODE"
}

INSTALL_STARTED=0
trap - EXIT HUP INT TERM

echo
echo "Stage 3B installation completed successfully."
echo "Backup: $BACKUP"
echo "Rollback command:"
echo "  systemctl stop $SERVICE && cp -a $BACKUP/app/api/routes.py $BACKEND/app/api/routes.py && cp -a $BACKUP/app/services/bms_pcs_control.py $BACKEND/app/services/bms_pcs_control.py && systemctl start $SERVICE"
echo
echo "Keep Flutter closed and run:"
echo "  sh $PKG_DIR/verify_stage3b.sh"
