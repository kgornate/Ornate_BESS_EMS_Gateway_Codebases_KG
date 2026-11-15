#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="/mnt/ems-logs/kinetics-gateway/automation_schedule.json"
STATUS="/mnt/ems-logs/kinetics-gateway/automation_status.json"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_1_5_$(date +%Y%m%d_%H%M%S)"
BASELINE_SHA="46f741c414e16a0aa3a4b5a7101c60451aaad720aac7e967d55b457e65c8bfb6"

cd "$ROOT_DIR"
sha256sum -c PAYLOAD_SHA256SUMS

[[ "${CONFIRM_IDLE:-}" == "YES" ]] || {
  echo "Disable automation, Save, verify all pairs stopped, then use CONFIRM_IDLE=YES."
  exit 2
}

[[ -f /root/bess_solar_time_automation.py ]] || {
  echo "Missing deployed scheduler baseline."
  exit 3
}
ACTUAL_SHA="$(sha256sum /root/bess_solar_time_automation.py | awk '{print $1}')"
[[ "$ACTUAL_SHA" == "$BASELINE_SHA" ]] || {
  echo "Phase 5.8.1.4 scheduler baseline mismatch."
  echo "Expected $BASELINE_SHA"
  echo "Actual   $ACTUAL_SHA"
  exit 3
}

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
        raise SystemExit("Scheduler still reports active/prepared pairs. Verify safe stop first.")
PY

systemctl stop bess-seven-day-v302.service
mkdir -p "$BACKUP_DIR/root"
cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"

cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/
cp -a \
  "$ROOT_DIR/payload/opt/kinetics-gateway/backend/tests/test_phase5_8_1_5_field_recovery.py" \
  /opt/kinetics-gateway/backend/tests/
chmod 755 /root/bess_solar_time_automation.py

/opt/kinetics-gateway/venv/bin/python -m py_compile \
  /root/bess_solar_time_automation.py
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_ready_by_scheduler.py \
  tests/test_phase5_8_1_3_reliability.py \
  tests/test_phase5_8_1_4_bounded_status.py \
  tests/test_phase5_8_1_5_field_recovery.py

systemctl start bess-seven-day-v302.service
for _ in $(seq 1 30); do
  if python3 - <<'PY'
import json
from pathlib import Path
p = Path("/mnt/ems-logs/kinetics-gateway/automation_status.json")
if not p.exists():
    raise SystemExit(1)
d = json.loads(p.read_text(encoding="utf-8"))
raise SystemExit(
    0 if d.get("build") == "phase5.8.1.5-bounded-field-recovery"
    and d.get("phase") == "disabled" else 1
)
PY
  then
    break
  fi
  sleep 2
done

python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text())
assert d.get("build") == "phase5.8.1.5-bounded-field-recovery", d
assert d.get("phase") == "disabled", d
assert d.get("active_pairs") == [], d
assert d.get("prepared_pairs") == [], d
print("Scheduler build, disabled state and empty pair ownership: OK")
PY

echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_1_5_latest
echo "Phase 5.8.1.5 installed. Automation remains disabled."

