#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"
sha256sum -c PAYLOAD_SHA256SUMS
python3 -m py_compile payload/root/bess_solar_time_automation.py \
  payload/opt/kinetics-gateway/backend/app/services/automation_schedule.py
grep -q '/api/control-sequence/control-view' payload/root/bess_solar_time_automation.py
grep -q '/api/control-sequence/safe-stop-all' payload/root/bess_solar_time_automation.py
grep -q '/api/control-sequence/.*/automatic-start' payload/root/bess_solar_time_automation.py
grep -q 'solar_active_power_kw' payload/opt/kinetics-gateway/backend/app/services/gateway_service.py
echo "Phase 5.8 payload verified."
