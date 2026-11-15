#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND=/opt/kinetics-gateway/backend
STATUS=/mnt/ems-logs/kinetics-gateway/automation_status.json
BACKUP="/opt/kinetics-gateway/backups/phase5_8_3_$(date +%Y%m%d_%H%M%S)"
ROUTES_BASELINE=c2198ce115653c661009ff660b1f5fd3cec8ea216d74961e18a1a373576cbce8

[ "${CONFIRM_IDLE:-}" = YES ] || {
    echo "Use CONFIRM_IDLE=YES after all pairs are stopped."
    exit 2
}

python3 - "$STATUS" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text()) if p.exists() else {}
if d.get("active_pairs") or d.get("prepared_pairs"):
    raise SystemExit("Scheduler still owns active/prepared pairs; wait for safe stop.")
PY

actual="$(sha256sum "$BACKEND/app/api/routes.py" | awk '{print $1}')"
[ "$actual" = "$ROUTES_BASELINE" ] || {
    echo "Phase 5.8.2.0 routes baseline mismatch. Actual: $actual"
    exit 3
}

mkdir -p "$BACKUP/app/api" "$BACKUP/tests"
cp -a "$BACKEND/app/api/routes.py" "$BACKUP/app/api/"
cp -a "$ROOT_DIR/backend/app/api/routes.py" "$BACKEND/app/api/routes.py"
cp -a "$ROOT_DIR/backend/tests/test_phase5_8_3_operator_timeline.py" "$BACKEND/tests/"

"$BACKEND/../venv/bin/python" -m py_compile "$BACKEND/app/api/routes.py"
cd "$BACKEND"
"$BACKEND/../venv/bin/python" -m pytest -q \
    tests/test_phase5_8_2_0_http_isolation.py \
    tests/test_phase5_8_3_operator_timeline.py

systemctl restart kinetics-gateway.service
for _ in $(seq 1 60); do
    curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
    sleep 2
done
curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null

echo "$BACKUP" > /opt/kinetics-gateway/backups/phase5_8_3_latest
echo "Gateway event-history API installed. Automation configuration was not changed."

if [ -n "${RTC_DEVICE:-}" ]; then
    [ -c "$RTC_DEVICE" ] || { echo "RTC device not found: $RTC_DEVICE"; exit 4; }
    install -m 0755 "$ROOT_DIR/system/kinetics-rtc-manager.sh" /root/kinetics-rtc-manager.sh
    install -m 0755 "$ROOT_DIR/system/kinetics-automation-time-guard.sh" /root/kinetics-automation-time-guard.sh
    install -m 0644 "$ROOT_DIR/system/kinetics-rtc-sync.service" /etc/systemd/system/kinetics-rtc-sync.service
    install -m 0644 "$ROOT_DIR/system/kinetics-rtc-save.service" /etc/systemd/system/kinetics-rtc-save.service
    install -m 0644 "$ROOT_DIR/system/kinetics-rtc-save.timer" /etc/systemd/system/kinetics-rtc-save.timer
    mkdir -p /etc/systemd/system/bess-seven-day-v302.service.d
    install -m 0644 "$ROOT_DIR/system/30-rtc-time-guard.conf" \
        /etc/systemd/system/bess-seven-day-v302.service.d/30-rtc-time-guard.conf
    printf 'RTC_DEVICE=%q\n' "$RTC_DEVICE" > /etc/kinetics-rtc.conf
    chmod 0600 /etc/kinetics-rtc.conf
    systemctl daemon-reload
    /root/kinetics-rtc-manager.sh save
    systemctl enable --now kinetics-rtc-sync.service kinetics-rtc-save.timer
    echo "External RTC configured on $RTC_DEVICE and seeded from validated system time."
else
    echo "RTC not changed. Detect first: bash $ROOT_DIR/detect_rtc.sh"
fi
