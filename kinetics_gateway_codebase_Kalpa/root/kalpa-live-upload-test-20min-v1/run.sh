#!/bin/bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
SOURCE=$(pwd)
sha256sum -c SHA256SUMS.txt
systemctl is-active --quiet ornate-central-sync.service || { echo "Central Sync must already be active."; exit 1; }
if systemctl is-active --quiet kalpa-sync-throughput-test.service; then
    echo "A different throughput test is active. Stop that test before running live-only observation."
    exit 1
fi
if systemctl is-active --quiet kalpa-live-upload-test.service; then
    echo "Live test already running."; exit 1
fi
REPORT="/root/kalpa-live-test-20min-$(date -u +%Y%m%dT%H%M%SZ)"
systemctl reset-failed kalpa-live-upload-test.service 2>/dev/null || true
systemd-run --unit=kalpa-live-upload-test --collect \
  --property=RuntimeMaxSec=22min --property=TimeoutStopSec=20s \
  --property=UMask=0077 --property=Environment=PYTHONUNBUFFERED=1 \
  /usr/bin/python3 "$SOURCE/test.py" --output "$REPORT"
echo "LIVE-ONLY test started; no outage/pause is introduced. SSH may disconnect."
echo "Results: $REPORT"
echo "Follow: journalctl -u kalpa-live-upload-test -f -o cat"
