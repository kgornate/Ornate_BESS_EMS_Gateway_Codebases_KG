#!/bin/bash
set -euo pipefail

year="$(date +%Y)"
if [ -f /run/kinetics-rtc-valid ] && [ "$year" -ge 2024 ] && [ "$year" -le 2100 ]; then
    echo "TIME-GUARD: validated external RTC time available; network time is optional"
    exit 0
fi

echo "TIME-GUARD: RTC fallback unavailable; using existing NTP/external validation"
exec /usr/local/sbin/bess-wait-for-valid-time.sh
