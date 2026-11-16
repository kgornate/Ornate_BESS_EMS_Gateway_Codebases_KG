#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
DURATION="${1:-1800}"
if [[ ! "$DURATION" =~ ^[0-9]+$ ]] || ((DURATION < 60 || DURATION > 86400)); then
    echo 'Duration must be 60..86400 seconds'; exit 1
fi
if systemctl is-active --quiet kalpa-archive-transport-validation.service; then
    echo 'A validation capture is already running; not starting another.'; exit 1
fi
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTPUT="/root/kalpa-archive-transport-validation-$STAMP"
systemd-run --unit=kalpa-archive-transport-validation --collect \
 --property=UMask=0077 --property="RuntimeMaxSec=$((DURATION + 180))" \
 /usr/bin/python3 "$PWD/observe.py" --output "$OUTPUT" --duration "$DURATION"
echo "Results: $OUTPUT.tar.gz"
echo 'Follow: journalctl -u kalpa-archive-transport-validation -f -o cat'
echo 'SSH may disconnect. No outage or upload pause is introduced.'
