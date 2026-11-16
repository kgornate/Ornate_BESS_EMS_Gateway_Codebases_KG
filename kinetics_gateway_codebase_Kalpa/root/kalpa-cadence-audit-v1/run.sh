#!/bin/bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
SOURCE=$(pwd)
sha256sum -c SHA256SUMS.txt
systemctl is-active --quiet ornate-central-sync.service || { echo "Central Sync must be active."; exit 1; }
if systemctl is-active --quiet kalpa-cadence-audit.service; then
  echo "Cadence audit already running; nothing changed."; exit 1
fi
DURATION=$(python3 - <<'PY'
from datetime import datetime,timedelta,timezone
import math
target=datetime(2026,11,3,11,0,tzinfo=timezone(timedelta(hours=5,minutes=30)))
seconds=math.ceil((target-datetime.now(timezone.utc)).total_seconds())
if not 60<=seconds<=86400:raise SystemExit('11 AM IST, 3 November must be within the next 24 hours.')
print(seconds)
PY
)
REPORT="/root/kalpa-cadence-audit-$(date -u +%Y%m%dT%H%M%SZ)"
systemctl reset-failed kalpa-cadence-audit.service 2>/dev/null || true
systemd-run --unit=kalpa-cadence-audit --collect \
  --property="RuntimeMaxSec=$((DURATION+1800))s" --property=TimeoutStopSec=120s \
  --property=UMask=0077 --property=Environment=PYTHONUNBUFFERED=1 \
  /usr/bin/python3 "$SOURCE/audit.py" --output "$REPORT" --duration "$DURATION"
printf '%s\n' "$REPORT" > /root/kalpa-cadence-results-path.txt
echo "Read-only cadence audit started; capture ends 11 AM IST, 3 November."
echo "Final analysis/compression may take a few minutes after capture ends."
echo "Existing overnight test and Central Sync remain unchanged."
echo "Results: $REPORT"
echo "Progress: journalctl -u kalpa-cadence-audit -f -o cat"
