#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"
sha256sum -c PAYLOAD_SHA256SUMS
python3 -m py_compile payload/root/bess_solar_time_automation.py \
  payload/opt/kinetics-gateway/backend/app/services/{automation_schedule.py,bms_pcs_control.py} \
  payload/opt/kinetics-gateway/backend/app/api/routes.py
grep -q 'automatic_prepared_zero_power' payload/opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
grep -q 'automatic-dispatch' payload/opt/kinetics-gateway/backend/app/api/routes.py
grep -q 'prepare_only' payload/root/bess_solar_time_automation.py
grep -q 'automation_events.jsonl' payload/root/bess_solar_time_automation.py
grep -q 'Revalidated immediately before power dispatch' payload/opt/kinetics-gateway/backend/app/services/bms_pcs_control.py
grep -q 'needsDetailedAutomaticRun' flutter_payload/lib/core/state/gateway_controller.dart
grep -q "runStatus == 'prepared'" flutter_payload/lib/models/gateway_models.dart
grep -q 'Phase 5.8.1 Flutter payload installed' install_flutter_phase5_8_1.ps1
echo "Phase 5.8.1 cumulative payload verified."
