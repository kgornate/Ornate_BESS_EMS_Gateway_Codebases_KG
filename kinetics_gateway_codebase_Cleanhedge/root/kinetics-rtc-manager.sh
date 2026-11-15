#!/bin/bash
set -euo pipefail

CONFIG=/etc/kinetics-rtc.conf
VALID_MARKER=/run/kinetics-rtc-valid
STATUS_FILE=/run/kinetics-rtc-status

list_rtc() {
    for node in /sys/class/rtc/rtc*; do
        [ -e "$node" ] || continue
        base="$(basename "$node")"
        name="$(cat "$node/name" 2>/dev/null || echo unknown)"
        hctosys="$(cat "$node/hctosys" 2>/dev/null || echo unknown)"
        printf '/dev/%s name=%s kernel_hctosys=%s\n' "$base" "$name" "$hctosys"
    done
}

load_config() {
    [ -r "$CONFIG" ] || {
        echo "RTC configuration missing: $CONFIG" >&2
        exit 2
    }
    # shellcheck source=/dev/null
    . "$CONFIG"
    : "${RTC_DEVICE:?RTC_DEVICE must be set in $CONFIG}"
    [ -c "$RTC_DEVICE" ] || {
        echo "Configured RTC is not a character device: $RTC_DEVICE" >&2
        exit 2
    }
}

rtc_epoch() {
    local rtc_text
    rtc_text="$(hwclock --show --utc --rtc="$RTC_DEVICE" 2>/dev/null)" || return 1
    date -d "$rtc_text" +%s
}

valid_epoch() {
    local epoch="$1" year
    year="$(date -u -d "@$epoch" +%Y)"
    [ "$year" -ge 2024 ] && [ "$year" -le 2100 ]
}

case "${1:-status}" in
    detect)
        list_rtc
        ;;
    boot)
        rm -f "$VALID_MARKER"
        load_config
        epoch="$(rtc_epoch || true)"
        if [ -z "$epoch" ] || ! valid_epoch "$epoch"; then
            echo "RTC invalid or unreadable: $RTC_DEVICE" | tee "$STATUS_FILE"
            exit 1
        fi
        hwclock --hctosys --utc --rtc="$RTC_DEVICE"
        printf 'device=%s source=rtc state=valid timestamp=%s\n' \
            "$RTC_DEVICE" "$(date -Is)" | tee "$STATUS_FILE"
        touch "$VALID_MARKER"
        ;;
    save)
        load_config
        system_epoch="$(date +%s)"
        valid_epoch "$system_epoch" || {
            echo "Refusing to write invalid system time to RTC" >&2
            exit 1
        }
        hwclock --systohc --utc --rtc="$RTC_DEVICE"
        printf 'device=%s source=system state=saved timestamp=%s\n' \
            "$RTC_DEVICE" "$(date -Is)" > "$STATUS_FILE"
        ;;
    status)
        list_rtc
        [ -r "$CONFIG" ] && cat "$CONFIG"
        [ -r "$STATUS_FILE" ] && cat "$STATUS_FILE"
        timedatectl status || true
        ;;
    *)
        echo "Usage: $0 {detect|boot|save|status}" >&2
        exit 2
        ;;
esac
