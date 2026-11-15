#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="/opt/kinetics-gateway/backend"
CONFIG="/mnt/ems-logs/kinetics-gateway/automation_schedule.json"
STATUS="/mnt/ems-logs/kinetics-gateway/automation_status.json"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_1_8_$(date +%Y%m%d_%H%M%S)"
SCHEDULER_BASELINE_SHA="0cc0409ab0ba684f0d6d881f81533664ea99d2719e1501a5af4c9e03cb43e61c"
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
[[ "$actual" == "$SCHEDULER_BASELINE_SHA" ]] || {
  echo "Phase 5.8.1.7 scheduler baseline mismatch."
  echo "Expected $SCHEDULER_BASELINE_SHA"
  echo "Actual   $actual"
  exit 3
}
systemctl stop bess-seven-day-v302.service
mkdir -p "$BACKUP_DIR/root"
cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"
cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/tests/test_phase5_8_1_8_runtime_resilience.py" "$BACKEND/tests/"
chmod 755 /root/bess_solar_time_automation.py
/opt/kinetics-gateway/venv/bin/python -m py_compile /root/bess_solar_time_automation.py
cd "$BACKEND"
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_http_admission.py \
  tests/test_phase5_8_1_3_reliability.py \
  tests/test_phase5_8_1_4_bounded_status.py \
  tests/test_phase5_8_1_7_pre_reset_recovery.py \
  tests/test_phase5_8_1_8_runtime_resilience.py
systemctl start bess-seven-day-v302.service
for _ in $(seq 1 30); do
  if python3 - <<'PY'
import json
from pathlib import Path
p = Path("/mnt/ems-logs/kinetics-gateway/automation_status.json")
if not p.exists(): raise SystemExit(1)
d = json.loads(p.read_text(encoding="utf-8"))
ok = d.get("build") == "phase5.8.1.8-runtime-resilience" and d.get("phase") == "disabled"
raise SystemExit(0 if ok else 1)
PY
  then break; fi
  sleep 2
done
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text())
assert d.get("build") == "phase5.8.1.8-runtime-resilience", d
assert d.get("phase") == "disabled", d
assert not d.get("active_pairs") and not d.get("prepared_pairs"), d
print("Scheduler build, disabled state and empty pair ownership: OK")
PY
echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_1_8_latest
echo "Phase 5.8.1.8 installed. Automation remains disabled."
