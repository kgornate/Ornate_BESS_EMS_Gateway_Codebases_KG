#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="/opt/kinetics-gateway/backend"
CONFIG="/mnt/ems-logs/kinetics-gateway/automation_schedule.json"
STATUS="/mnt/ems-logs/kinetics-gateway/automation_status.json"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_2_0_$(date +%Y%m%d_%H%M%S)"
SCHEDULER_PHASE_5_8_1_8_SHA="d95d7c2fbd150a05a403444e35ff6c1a525fbb94d91a5bde63cc6adfbb2efbdc"
SCHEDULER_FIELD_HOTFIX_SHA="282669294ed4ca96da3c89923de9cc302d751d2a1fb8dc071f42dd30b63e96db"
ROUTES_BASELINE_SHA="04ac3009da7d951bb640071219e9260ffa35391e066f5a15ceb763b2dcf997a0"
cd "$ROOT_DIR"
sha256sum -c PAYLOAD_SHA256SUMS
[[ "${CONFIRM_IDLE:-}" == "YES" ]] || { echo "Disable automation, Save, verify all pairs stopped, then use CONFIRM_IDLE=YES."; exit 2; }
python3 - "$CONFIG" "$STATUS" <<'PY'
import json, sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if config.get("enabled"):
    raise SystemExit("Automation remains enabled. Disable it in Flutter and Save first.")
status_path = Path(sys.argv[2])
if status_path.exists():
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("active_pairs") or status.get("prepared_pairs"):
        raise SystemExit("Scheduler still owns active/prepared pairs. Verify safe stop first.")
PY
actual="$(sha256sum /root/bess_solar_time_automation.py | awk '{print $1}')"
[[ "$actual" == "$SCHEDULER_PHASE_5_8_1_8_SHA" || "$actual" == "$SCHEDULER_FIELD_HOTFIX_SHA" ]] || {
  echo "Phase 5.8.1.8 scheduler baseline mismatch."
  echo "Expected $SCHEDULER_PHASE_5_8_1_8_SHA or field hotfix $SCHEDULER_FIELD_HOTFIX_SHA"
  echo "Actual   $actual"
  exit 3
}
routes_actual="$(sha256sum "$BACKEND/app/api/routes.py" | awk '{print $1}')"
[[ "$routes_actual" == "$ROUTES_BASELINE_SHA" ]] || {
  echo "Gateway routes baseline mismatch."
  echo "Expected $ROUTES_BASELINE_SHA"
  echo "Actual   $routes_actual"
  exit 3
}
systemctl stop bess-seven-day-v302.service
systemctl stop kinetics-gateway.service
mkdir -p "$BACKUP_DIR/root" "$BACKUP_DIR/backend/app/api" "$BACKUP_DIR/backend/tests"
cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"
cp -a "$BACKEND/app/api/routes.py" "$BACKUP_DIR/backend/app/api/"
cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/app/api/routes.py" "$BACKEND/app/api/"
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/tests/test_phase5_8_2_0_http_isolation.py" "$BACKEND/tests/"
chmod 755 /root/bess_solar_time_automation.py
/opt/kinetics-gateway/venv/bin/python -m py_compile \
  /root/bess_solar_time_automation.py \
  "$BACKEND/app/api/routes.py"
cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_http_admission.py \
  tests/test_phase5_8_1_3_reliability.py \
  tests/test_phase5_8_1_4_bounded_status.py \
  tests/test_phase5_8_1_7_pre_reset_recovery.py \
  tests/test_phase5_8_1_8_runtime_resilience.py \
  tests/test_phase5_8_2_0_http_isolation.py
systemctl start kinetics-gateway.service
for _ in $(seq 1 60); do
  curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null
systemctl start bess-seven-day-v302.service
for _ in $(seq 1 30); do
  if python3 - <<'PY'
import json
from pathlib import Path
p = Path("/mnt/ems-logs/kinetics-gateway/automation_status.json")
if not p.exists(): raise SystemExit(1)
d = json.loads(p.read_text(encoding="utf-8"))
ok = d.get("build") == "phase5.8.2.0-http-isolation" and d.get("phase") == "disabled"
raise SystemExit(0 if ok else 1)
PY
  then break; fi
  sleep 2
done
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text())
assert d.get("build") == "phase5.8.2.0-http-isolation", d
assert d.get("snapshot_request_timeout_seconds") == 15.0, d
assert d.get("phase") == "disabled", d
assert not d.get("active_pairs") and not d.get("prepared_pairs"), d
print("Scheduler build, disabled state and empty pair ownership: OK")
PY
echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_2_0_latest
echo "Phase 5.8.2.0 installed. Automation remains disabled."
