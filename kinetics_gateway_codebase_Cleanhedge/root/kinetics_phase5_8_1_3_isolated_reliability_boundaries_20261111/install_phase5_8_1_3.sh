#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_1_3_$(date +%Y%m%d_%H%M%S)"
CONFIG="/mnt/ems-logs/kinetics-gateway/automation_schedule.json"
cd "$ROOT_DIR"
sha256sum -c PAYLOAD_SHA256SUMS

[[ "${CONFIRM_IDLE:-}" == "YES" ]] || {
  echo "Disable automation, Save, verify all pairs stopped, then use CONFIRM_IDLE=YES."
  exit 2
}

check() {
  local expected="$1" path="$2" actual
  [[ -f "$path" ]] || { echo "Missing deployed baseline: $path"; exit 3; }
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "Phase 5.8.1.2 baseline mismatch: $path"
    echo "Expected $expected"
    echo "Actual   $actual"
    exit 3
  }
}

check 28554580cf0ffcbcc571221599734065c53cd08ce8ae9c1732754bae03ce5022 /root/bess_solar_time_automation.py
check 60673ecd822f23375d77a77f77494a9cf7b462bd8b0bdf563c94c5f198778ab9 /opt/kinetics-gateway/backend/app/main.py
check 06aeef27ba311a7a846c9227087648f915304da1e8a63f21de5ce8af8ed46945 /opt/kinetics-gateway/backend/app/api/routes.py
check 42d48d8e1589753fea2fc5b482dc412c9f18346872d87109cef9718a998ad13f /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
check 14fdc61bf7d6685b6c4c97105e515df621bca204d4d1afcb3362c5d11bce069f /opt/kinetics-gateway/backend/app/services/gateway_service.py
check a749d7d0687e3985bd5ac0ca62b771ba215b02d046f61b745a36a10665c0c342 /opt/kinetics-gateway/backend/app/services/http_admission.py

python3 - "$CONFIG" <<'PY'
import json, sys
config = json.load(open(sys.argv[1], encoding="utf-8"))
if config.get("enabled"):
    raise SystemExit("Automation remains enabled. Disable it in Flutter and Save first.")
PY

systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
mkdir -p "$BACKUP_DIR/backend/app/services" "$BACKUP_DIR/backend/app/api" "$BACKUP_DIR/backend/tests" "$BACKUP_DIR/root"
cp -a /opt/kinetics-gateway/backend/app/main.py "$BACKUP_DIR/backend/app/"
cp -a /opt/kinetics-gateway/backend/app/api/routes.py "$BACKUP_DIR/backend/app/api/"
cp -a /opt/kinetics-gateway/backend/app/services/{bms_pcs_control.py,gateway_service.py,http_admission.py} "$BACKUP_DIR/backend/app/services/"
cp -a /opt/kinetics-gateway/backend/tests/{test_http_admission.py,test_phase5_8_1_3_reliability.py} "$BACKUP_DIR/backend/tests/" 2>/dev/null || true
cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"

cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/app/main.py" /opt/kinetics-gateway/backend/app/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/app/api/routes.py" /opt/kinetics-gateway/backend/app/api/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/app/services/." /opt/kinetics-gateway/backend/app/services/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/tests/." /opt/kinetics-gateway/backend/tests/
cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/
chmod 755 /root/bess_solar_time_automation.py

cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m py_compile \
  app/main.py app/api/routes.py app/services/bms_pcs_control.py \
  app/services/gateway_service.py app/services/http_admission.py \
  /root/bess_solar_time_automation.py
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_http_admission.py tests/test_phase5_8_1_3_reliability.py

systemctl start kinetics-gateway.service
for _ in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import importlib.util, time
spec = importlib.util.spec_from_file_location("scheduler", "/root/bess_solar_time_automation.py")
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
gateway = module.Gateway(); gateway.login()
started = time.monotonic()
snapshot = gateway.request("GET", "/api/internal/control-sequence/automation-snapshot", timeout=4)
elapsed = time.monotonic() - started
assert snapshot.get("contract") == "phase5.8.1.3-automation-snapshot-v2", snapshot
assert set((snapshot.get("by_pair_id") or {}).keys()) == {"pair_1", "pair_2", "pair_3", "pair_4"}
assert elapsed < 2.0, elapsed
print(f"Isolated automation snapshot: OK ({elapsed:.3f}s)")
PY

systemctl start bess-seven-day-v302.service
for _ in $(seq 1 20); do
  if python3 - <<'PY'
import json
from pathlib import Path
p=Path("/mnt/ems-logs/kinetics-gateway/automation_status.json")
if not p.exists(): raise SystemExit(1)
d=json.loads(p.read_text())
raise SystemExit(0 if d.get("build")=="phase5.8.1.3-isolated-reliability-boundaries" and d.get("phase")=="disabled" else 1)
PY
  then break; fi
  sleep 2
done
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text())
assert d.get("build") == "phase5.8.1.3-isolated-reliability-boundaries", d
assert d.get("phase") == "disabled", d
print("Scheduler build and disabled state: OK")
PY

echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_1_3_latest
echo "Phase 5.8.1.3 installed. Automation remains disabled."
