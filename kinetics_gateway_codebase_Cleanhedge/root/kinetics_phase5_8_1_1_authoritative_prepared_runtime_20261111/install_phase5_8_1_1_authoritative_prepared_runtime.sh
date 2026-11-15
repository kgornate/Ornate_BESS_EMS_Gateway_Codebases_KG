#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_1_1_$(date +%Y%m%d_%H%M%S)"
CONFIG="/mnt/ems-logs/kinetics-gateway/automation_schedule.json"

[[ "${CONFIRM_IDLE:-}" == "YES" ]] || {
  echo "Disable automation, Save settings, run Safe Stop All, verify every pair stopped, then use CONFIRM_IDLE=YES."
  exit 2
}

check() {
  local expected="$1" path="$2" actual
  [[ -f "$path" ]] || { echo "Missing Phase 5.8.1 baseline: $path"; exit 3; }
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "Phase 5.8.1 baseline mismatch: $path"
    echo "Expected $expected"
    echo "Actual   $actual"
    exit 3
  }
}

check 83ecc440ef9797ce9f37840ad74d4965c9a541126de363ecf542aa71719e2770 /root/bess_solar_time_automation.py
check 3c9c71058bc11f263227184a85a8b2409325772c699fba5117b98bbb2a6adfb3 /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
check 5360cd99063be6149415f57e34390fe9cb1d749e7936f3003037e7c4a1f995b7 /opt/kinetics-gateway/backend/app/services/gateway_service.py
check b355697a2d0772e2bdb195ae9ca357eabd2b4cba4638e2c8837fa14136b15b32 /opt/kinetics-gateway/backend/app/api/routes.py
check de42d28ec6c479bf065ed871a68bbf6e405a3d44f0e567ea1c3e297b362cebdd /opt/kinetics-gateway/backend/app/services/automation_schedule.py

[[ -f "$CONFIG" ]] || { echo "Automation configuration is missing: $CONFIG"; exit 4; }
python3 - "$CONFIG" <<'PY'
import json, sys
config = json.load(open(sys.argv[1], encoding="utf-8"))
if config.get("enabled"):
    raise SystemExit("Automation is still enabled. Turn it off in Flutter and press Save before installing.")
PY

systemctl stop bess-seven-day-v302.service 2>/dev/null || true
mkdir -p "$BACKUP_DIR/root"
cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"
cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/
chmod 755 /root/bess_solar_time_automation.py

python3 -m py_compile /root/bess_solar_time_automation.py
python3 "$ROOT_DIR/tests/test_phase5_8_1_1_scheduler.py" -v
systemctl start bess-seven-day-v302.service
sleep 3
systemctl is-active --quiet bess-seven-day-v302.service

for _ in $(seq 1 10); do
  if python3 - <<'PY'
import json
from pathlib import Path
p = Path("/mnt/ems-logs/kinetics-gateway/automation_status.json")
if not p.exists():
    raise SystemExit(1)
d = json.loads(p.read_text(encoding="utf-8"))
raise SystemExit(0 if d.get("build") == "phase5.8.1.1-authoritative-prepared-runtime" else 1)
PY
  then break; fi
  sleep 2
done

python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text(encoding="utf-8"))
assert d.get("build") == "phase5.8.1.1-authoritative-prepared-runtime", d
assert d.get("phase") == "disabled", d
print("Scheduler build:", d.get("build"))
print("Scheduler phase:", d.get("phase"))
PY

echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_1_1_latest
echo "Phase 5.8.1.1 installed. Automation remains disabled; enable it only when ready to retest."
