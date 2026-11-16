#!/bin/bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
SOURCE=$(pwd)
sha256sum -c SHA256SUMS.txt
systemctl is-active --quiet ornate-central-sync.service || {
    echo "Central Sync must already be active; nothing changed."; exit 1;
}
for UNIT in kalpa-overnight-test.service kalpa-live-upload-test.service kalpa-sync-throughput-test.service; do
    if systemctl is-active --quiet "$UNIT"; then
        echo "Another test is running: $UNIT; nothing changed."; exit 1
    fi
done

# Explicit deadline requested by Kunal, using IST independent of device timezone.
DURATION=$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
import math
target=datetime(2026,11,3,11,0,tzinfo=timezone(timedelta(hours=5,minutes=30)))
seconds=math.ceil((target-datetime.now(timezone.utc)).total_seconds())
if not 60<=seconds<=24*3600:
    raise SystemExit('11 AM IST on 3 November is not within the next 24 hours; nothing started.')
print(seconds)
PY
)
REPORT="/root/kalpa-overnight-test-$(date -u +%Y%m%dT%H%M%SZ)"
LIMIT=$((DURATION + 300))
systemctl reset-failed kalpa-overnight-test.service 2>/dev/null || true
systemd-run --unit=kalpa-overnight-test --collect \
  --property="RuntimeMaxSec=${LIMIT}s" --property=TimeoutStopSec=40s \
  --property=UMask=0077 --property=Environment=PYTHONUNBUFFERED=1 \
  /usr/bin/python3 "$SOURCE/test.py" --output "$REPORT" \
    --duration "$DURATION" --interval 30
printf '%s\n' "$REPORT" > /root/kalpa-overnight-results-path.txt
echo "Overnight capture started; finishes at 11:00 AM IST, 3 November."
echo "SSH may disconnect. Central Sync settings are unchanged."
echo "Results: $REPORT"
echo "Progress: journalctl -u kalpa-overnight-test -f -o cat"
