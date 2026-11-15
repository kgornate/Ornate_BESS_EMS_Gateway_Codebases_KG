#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"
sha256sum -c PAYLOAD_SHA256SUMS
python3 -m py_compile \
  payload/root/bess_solar_time_automation.py \
  payload/opt/kinetics-gateway/backend/app/main.py \
  payload/opt/kinetics-gateway/backend/app/api/routes.py \
  payload/opt/kinetics-gateway/backend/app/services/{bms_pcs_control.py,gateway_service.py,http_admission.py}
grep -q 'phase5.8.1.3-isolated-reliability-boundaries' payload/root/bess_solar_time_automation.py
grep -q 'phase5.8.1.3-automation-snapshot-v2' payload/opt/kinetics-gateway/backend/app/api/routes.py
grep -q 'scheduler snapshot circuit open' payload/root/bess_solar_time_automation.py
grep -q 'HttpLane = Literal' payload/opt/kinetics-gateway/backend/app/services/http_admission.py
grep -q '_scheduler_telemetry_cache' payload/opt/kinetics-gateway/backend/app/services/gateway_service.py
echo "Phase 5.8.1.3 payload verified."
