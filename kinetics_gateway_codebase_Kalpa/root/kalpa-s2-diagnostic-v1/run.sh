#!/bin/bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
sha256sum -c SHA256SUMS.txt
systemctl is-active --quiet ornate-central-sync.service || { echo "Central Sync is not active; nothing changed."; exit 1; }
if systemctl is-active --quiet kalpa-s2-diagnostic.service; then echo "Diagnostic already running."; exit 1; fi
REPORT="/root/kalpa-s2-diagnostic-$(date -u +%Y%m%dT%H%M%SZ)"
systemctl reset-failed kalpa-s2-diagnostic.service 2>/dev/null || true
systemd-run --unit=kalpa-s2-diagnostic --collect \
  --property=RuntimeMaxSec=8min --property=TimeoutStopSec=40s \
  --property=UMask=0077 --property=Environment=PYTHONUNBUFFERED=1 \
  /usr/bin/python3 "$(pwd)/capture.py" --output "$REPORT"
echo "Read-only five-minute capture started. SSH may disconnect; no existing service restarted."
echo "Results: $REPORT.tar.gz"
echo "Follow: journalctl -u kalpa-s2-diagnostic -f -o cat"
