#!/bin/bash
set -euo pipefail
echo "Detected RTC devices:"
for node in /sys/class/rtc/rtc*; do
    [ -e "$node" ] || continue
    base="$(basename "$node")"
    printf '/dev/%s  name=%s  hctosys=%s\n' \
        "$base" \
        "$(cat "$node/name" 2>/dev/null || echo unknown)" \
        "$(cat "$node/hctosys" 2>/dev/null || echo unknown)"
    hwclock --show --utc --rtc="/dev/$base" 2>&1 || true
done
