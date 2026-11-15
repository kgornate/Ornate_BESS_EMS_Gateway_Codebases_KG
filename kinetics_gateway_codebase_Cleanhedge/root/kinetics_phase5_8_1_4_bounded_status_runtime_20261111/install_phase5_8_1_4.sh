#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/kinetics-gateway/backups/phase5_8_1_4_$(date +%Y%m%d_%H%M%S)"
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
    echo "Phase 5.8.1.3 baseline mismatch: $path"
    echo "Expected $expected"
    echo "Actual   $actual"
    exit 3
  }
}

check d57d06b704f9dec70c7bbfa24959f8aa8b13a945f8269eeaeea83fb7ceb9f289 /root/bess_solar_time_automation.py
check 82a6738fff4bfcd73062de6683ffa9b5faae0864424048e80aed14dd1eb10a9b /opt/kinetics-gateway/backend/app/main.py
check 28cc9073130f2f8360897532e280f66ee46252fffa68c338a03ad69a80053b7a /opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
check 4ec9ed8c87cfb0b374154dbb2a7986d2e5ed7fe7f84db6f16fad5c95f3b47da9 /opt/kinetics-gateway/backend/app/services/gateway_service.py

python3 - "$CONFIG" <<'PY'
import json, sys
config = json.load(open(sys.argv[1], encoding="utf-8"))
if config.get("enabled"):
    raise SystemExit("Automation remains enabled. Disable it in Flutter and Save first.")
PY

systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
mkdir -p "$BACKUP_DIR/backend/app/services" "$BACKUP_DIR/backend/tests" "$BACKUP_DIR/root"
cp -a /opt/kinetics-gateway/backend/app/main.py "$BACKUP_DIR/backend/app/"
cp -a /opt/kinetics-gateway/backend/app/services/{bms_pcs_control.py,gateway_service.py} "$BACKUP_DIR/backend/app/services/"
cp -a /root/bess_solar_time_automation.py "$BACKUP_DIR/root/"

cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/app/main.py" /opt/kinetics-gateway/backend/app/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/app/services/bms_pcs_control.py" /opt/kinetics-gateway/backend/app/services/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/app/services/gateway_service.py" /opt/kinetics-gateway/backend/app/services/
cp -a "$ROOT_DIR/payload/opt/kinetics-gateway/backend/tests/test_phase5_8_1_4_bounded_status.py" /opt/kinetics-gateway/backend/tests/
cp -a "$ROOT_DIR/payload/root/bess_solar_time_automation.py" /root/
chmod 755 /root/bess_solar_time_automation.py

cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m py_compile \
  app/main.py app/services/bms_pcs_control.py app/services/gateway_service.py \
  /root/bess_solar_time_automation.py
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_http_admission.py tests/test_phase5_8_1_4_bounded_status.py

systemctl start kinetics-gateway.service
for _ in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null

/opt/kinetics-gateway/venv/bin/python - <<'PY'
import concurrent.futures
import importlib.util
import time

spec = importlib.util.spec_from_file_location("scheduler", "/root/bess_solar_time_automation.py")
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
gateway = module.Gateway(); gateway.login()

def read(pair_id):
    started = time.monotonic()
    result = gateway.request("GET", f"/api/control-sequence/{pair_id}/status?fresh=false", timeout=3)
    return time.monotonic() - started, result

pair_ids = [f"pair_{index}" for index in range(1, 5)] * 8
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    results = list(executor.map(read, pair_ids))
elapsed = [item[0] for item in results]
assert max(elapsed) < 3.0, elapsed
assert all("runtime" in item[1] for item in results)

started = time.monotonic()
snapshot = gateway.request("GET", "/api/internal/control-sequence/automation-snapshot", timeout=4)
snapshot_elapsed = time.monotonic() - started
assert snapshot.get("contract") == "phase5.8.1.3-automation-snapshot-v2", snapshot
assert snapshot_elapsed < 2.0, snapshot_elapsed
print(f"32 concurrent cache-only status reads: OK (max={max(elapsed):.3f}s)")
print(f"Isolated automation snapshot: OK ({snapshot_elapsed:.3f}s)")
PY

systemctl start bess-seven-day-v302.service
for _ in $(seq 1 20); do
  if python3 - <<'PY'
import json
from pathlib import Path
p=Path("/mnt/ems-logs/kinetics-gateway/automation_status.json")
if not p.exists(): raise SystemExit(1)
d=json.loads(p.read_text())
raise SystemExit(0 if d.get("build")=="phase5.8.1.4-bounded-status-runtime" and d.get("phase")=="disabled" else 1)
PY
  then break; fi
  sleep 2
done
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text())
assert d.get("build") == "phase5.8.1.4-bounded-status-runtime", d
assert d.get("phase") == "disabled", d
print("Scheduler build and disabled state: OK")
PY

echo "$BACKUP_DIR" > /opt/kinetics-gateway/backups/phase5_8_1_4_latest
echo "Phase 5.8.1.4 installed. Automation remains disabled."
