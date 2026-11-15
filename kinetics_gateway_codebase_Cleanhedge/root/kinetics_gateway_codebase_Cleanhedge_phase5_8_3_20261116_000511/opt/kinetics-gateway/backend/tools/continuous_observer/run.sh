#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
python3 - <<'PY'
import hashlib,json
from pathlib import Path
for name,digest in json.loads(Path('checksums.json').read_text()).items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest:
        raise SystemExit('Package checksum mismatch: '+name)
for name in ('capture.py','probe.py','test_capture.py'):
    compile(Path(name).read_bytes(),name,'exec')
PY
python3 -B -m unittest test_capture -q
systemctl is-active --quiet kinetics-central-sync.service
if systemctl is-active --quiet cleanedge-central-sync-observer.service; then
    echo 'Continuous v2.2 capture already active; unchanged.'
    exit 0
fi
# Replace only temporary v2.2 observers, preserving the existing 48-hour capture.
while read -r UNIT; do
    case "$UNIT" in
      cleanedge-central-sync-observer-*.service)
        systemctl stop "$UNIT"
        echo "Stopped temporary observer: $UNIT (partial files retained)"
        ;;
    esac
done < <(systemctl list-units --type=service --state=running --no-legend --plain --full 'cleanedge-central-sync-observer-*' | awk '{print $1}')
systemd-run --unit=cleanedge-central-sync-observer --collect \
    --property=UMask=0077 --property=RuntimeMaxSec=90000 \
    --property=TimeoutStopSec=300 \
    /usr/bin/python3 -B -u "$PWD/capture.py" --duration 86400
printf '%s\n' 'Follow: journalctl -u cleanedge-central-sync-observer -f -o cat'
printf '%s\n' 'No production service restarted; existing long observation unchanged.'

