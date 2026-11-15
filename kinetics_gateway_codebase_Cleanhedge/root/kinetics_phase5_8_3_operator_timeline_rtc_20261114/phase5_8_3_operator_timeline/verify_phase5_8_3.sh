#!/bin/bash
set -euo pipefail
systemctl is-active kinetics-gateway.service
curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q \
    tests/test_phase5_8_2_0_http_isolation.py \
    tests/test_phase5_8_3_operator_timeline.py
echo "Phase 5.8.3 gateway verification: OK"
if [ -f /etc/kinetics-rtc.conf ]; then
    /root/kinetics-rtc-manager.sh status
fi
