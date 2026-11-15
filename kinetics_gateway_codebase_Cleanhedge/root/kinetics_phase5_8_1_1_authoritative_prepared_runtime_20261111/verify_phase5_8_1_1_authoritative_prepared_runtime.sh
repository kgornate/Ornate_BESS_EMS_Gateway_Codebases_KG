#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"
sha256sum -c PAYLOAD_SHA256SUMS
python3 -m py_compile payload/root/bess_solar_time_automation.py
python3 tests/test_phase5_8_1_1_scheduler.py -v
grep -q 'phase5.8.1.1-authoritative-prepared-runtime' payload/root/bess_solar_time_automation.py
grep -q 'matching_prepared_runtime' payload/root/bess_solar_time_automation.py
grep -q 'automatic retries inhibited' payload/root/bess_solar_time_automation.py
echo "Phase 5.8.1.1 focused scheduler patch verified."
