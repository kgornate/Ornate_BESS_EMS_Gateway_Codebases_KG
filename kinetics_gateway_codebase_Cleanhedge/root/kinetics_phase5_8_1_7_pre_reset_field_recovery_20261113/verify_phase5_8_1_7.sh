#!/bin/bash
set -euo pipefail
systemctl is-active kinetics-gateway.service
systemctl is-active bess-seven-day-v302.service
curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null
/opt/kinetics-gateway/venv/bin/python -m py_compile /root/bess_solar_time_automation.py
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
  tests/test_http_admission.py \
  tests/test_phase5_8_1_7_pre_reset_recovery.py
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text())
assert d.get("build") == "phase5.8.1.7-pre-reset-field-recovery", d
assert "snapshot_loss_safe_stop_seconds" in d, d
print("Phase 5.8.1.7 verification: OK")
PY
