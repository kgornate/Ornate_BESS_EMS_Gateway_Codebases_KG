#!/bin/bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
SOURCE=$(pwd)
/opt/ornate-ems-gateway/venv/bin/python install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python install.py
DELAY=$(python3 - <<'PY'
from datetime import datetime,timezone
import math
target=datetime(2026,11,3,5,32,tzinfo=timezone.utc)
seconds=math.ceil((target-datetime.now(timezone.utc)).total_seconds())
if seconds<1:raise SystemExit('Automatic report time has passed; use report.py manually.')
print(seconds)
PY
)
REPORT="/root/kalpa-request-timing-results-$(date -u +%Y%m%dT%H%M%SZ)"
printf '%s\n' "$REPORT" > /root/kalpa-request-timing-results-path.txt
systemd-run --unit="kalpa-request-timing-report-$(date -u +%H%M%S)" \
  --on-active="${DELAY}s" --collect --property=UMask=0077 \
  /usr/bin/python3 "$SOURCE/report.py" --output "$REPORT"
echo "Timing enabled through 11 AM IST. Report export scheduled for approximately 11:02 AM IST."
echo "Results: $REPORT.tar.gz"
