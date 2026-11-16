#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if systemctl is-active --quiet kalpa-archive-transport-validation.service; then
    echo 'Existing validation capture still active. Wait for it to finish; nothing changed.'; exit 1
fi
/opt/ornate-ems-gateway/venv/bin/python -B test_cleanup.py
/opt/ornate-ems-gateway/venv/bin/python -B install_cleanup.py
# Extend and restart the existing recorder to cover the entire next 24 hours.
if systemctl is-active --quiet kalpa-delivery-recorder.service; then
    systemctl set-property --runtime kalpa-delivery-recorder.service RuntimeMaxSec=90000
    systemctl restart kalpa-delivery-recorder.service
    echo 'Dashboard recorder restarted; existing log retained; runtime limit 25 hours.'
else
    echo 'Dashboard recorder inactive; creating a fresh 25-hour recording.'
    install -d -m700 /root/kalpa-dashboard-logs
    LOG="/root/kalpa-dashboard-logs/delivery-$(date -u +%Y%m%dT%H%M%SZ).log"
    systemd-run --unit="kalpa-delivery-recorder-24h-$(date -u +%Y%m%dT%H%M%SZ)" --collect \
      --property=UMask=0077 --property=RuntimeMaxSec=90000 \
      --property="StandardOutput=append:$LOG" --property="StandardError=append:$LOG" \
      /usr/local/bin/kalpa-delivery-dashboard --interval 15
    printf '%s\n' "$LOG" > /root/kalpa-dashboard-logs/current-path.txt
fi
bash observe.sh 86400
echo '24-hour capture launched. Keep gateway power and Internet available.'
echo 'This capture is diagnostic; no upload/cadence policy changes or planned outages.'
