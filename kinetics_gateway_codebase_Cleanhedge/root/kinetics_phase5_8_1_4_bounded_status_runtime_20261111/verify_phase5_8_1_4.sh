#!/bin/bash
set -euo pipefail
systemctl is-active kinetics-gateway.service
systemctl is-active bess-seven-day-v302.service
curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q tests/test_phase5_8_1_4_bounded_status.py
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_status.json").read_text())
assert d.get("build") == "phase5.8.1.4-bounded-status-runtime", d
print("Phase 5.8.1.4 verification: OK")
PY
