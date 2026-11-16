#!/bin/bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
SOURCE=$(pwd)
sha256sum -c SHA256SUMS.txt
if systemctl is-active --quiet kalpa-ack-capture.service; then echo "ACK companion already running."; exit 1; fi
test -f /root/kalpa-overnight-results-path.txt
DURATION=$(python3 - <<'PY'
from datetime import datetime,timezone
import math
seconds=math.ceil((datetime(2026,11,3,5,30,tzinfo=timezone.utc)-datetime.now(timezone.utc)).total_seconds())
if not 60<=seconds<=86400:raise SystemExit('Capture deadline is not within the next 24 hours.')
print(seconds)
PY
)
REPORT="/root/kalpa-ack-capture-$(date -u +%Y%m%dT%H%M%SZ)"
systemctl reset-failed kalpa-ack-capture.service 2>/dev/null || true
systemd-run --unit=kalpa-ack-capture --collect --property="RuntimeMaxSec=$((DURATION+300))s" \
  --property=TimeoutStopSec=60s --property=UMask=0077 --property=Environment=PYTHONUNBUFFERED=1 \
  /usr/bin/python3 "$SOURCE/capture.py" --output "$REPORT" --duration "$DURATION"
printf '%s\n' "$REPORT" > /root/kalpa-ack-capture-results-path.txt
echo "Companion started; no existing service restarted or changed. Ends 11 AM IST."
echo "Results: $REPORT.tar.gz"
echo "Follow: journalctl -u kalpa-ack-capture -f -o cat"
