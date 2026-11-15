#!/bin/sh

set -eu

EMS_MOUNT="/mnt/ems-logs"
STATE_FILE="/mnt/ems-logs/scheduler/bess-seven-day-backend-v3/state.json"
STAMP="$(date +%Y%m%d_%H%M%S)"
DEST="$EMS_MOUNT/backups/root-offload-$STAMP"

echo "============================================================"
echo "ORNATE EMS ROOT STORAGE CLEANUP"
echo "============================================================"
echo

echo "1. Checking EMS storage mount..."

if ! mountpoint -q "$EMS_MOUNT"; then
    echo "ERROR: $EMS_MOUNT is not mounted."
    echo "Cleanup aborted."
    exit 1
fi

EMS_FREE_KB="$(df -Pk "$EMS_MOUNT" | awk 'NR == 2 {print $4}')"

if [ "$EMS_FREE_KB" -lt 5242880 ]; then
    echo "ERROR: Less than 5 GB is available on $EMS_MOUNT."
    echo "Cleanup aborted."
    exit 1
fi

echo "EMS storage is mounted."
df -h / "$EMS_MOUNT"

echo
echo "2. Checking scheduler phase..."

if [ -f "$STATE_FILE" ]; then
    PHASE="$(
        python3 - "$STATE_FILE" <<'PY'
import json
import sys

path = sys.argv[1]

try:
    with open(path, "r", encoding="utf-8") as file:
        state = json.load(file)

    print(state.get("phase", "unknown"))
except Exception:
    print("unknown")
PY
    )"

    echo "Scheduler phase: $PHASE"

    case "$PHASE" in
        waiting_*|completed|unknown)
            ;;
        *)
            echo
            echo "ERROR: Scheduler may be preparing, charging, discharging or stopping."
            echo "Cleanup aborted to avoid interference."
            exit 1
            ;;
    esac
else
    echo "Scheduler state file not found; continuing cautiously."
fi

echo
echo "3. Creating offload directory..."

mkdir -p "$DEST"

echo "Destination:"
echo "$DEST"

echo
echo "4. Moving duplicate gateway backups from /root..."

move_item()
{
    ITEM="$1"

    if [ -e "$ITEM" ] || [ -L "$ITEM" ]; then
        echo "Moving: $ITEM"
        mv "$ITEM" "$DEST/"
    else
        echo "Not present: $ITEM"
    fi
}

move_item /root/kinetics_gateway_latest_release
move_item /root/kinetics_gateway_v3_20260731T083308Z
move_item /root/kinetics_gateway_latest_release.tar.gz
move_item /root/kinetics_gateway_latest_running.tar.gz
move_item /root/kinetics_before_memory_fix_20260728_154025
move_item /root/kinetics_stage2_review_20260728_154405

echo
echo "5. Removing pip download cache..."

if [ -d /root/.cache/pip ]; then
    rm -rf /root/.cache/pip
    echo "Pip cache removed."
else
    echo "No pip cache found."
fi

echo
echo "6. Removing Python cache files..."

find /root /opt/kinetics-gateway \
    -xdev \
    -type d \
    -name '__pycache__' \
    -prune \
    -exec rm -rf {} + \
    2>/dev/null || true

find /root /opt/kinetics-gateway \
    -xdev \
    -type f \
    \( -name '*.pyc' -o -name '*.pyo' \) \
    -delete \
    2>/dev/null || true

echo "Python caches removed."

echo
echo "7. Limiting system journal to 50 MB..."

journalctl --vacuum-size=50M || true

echo
echo "8. Synchronizing filesystem..."

sync

echo
echo "9. Checking for deleted files still held open..."

python3 <<'PY'
import os

records = []

for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue

    fd_directory = f"/proc/{pid}/fd"

    try:
        descriptors = os.listdir(fd_directory)
    except OSError:
        continue

    try:
        with open(
            f"/proc/{pid}/cmdline",
            "rb",
        ) as file:
            command = (
                file.read()
                .replace(b"\x00", b" ")
                .decode(errors="replace")
                .strip()
            )
    except OSError:
        command = ""

    for descriptor in descriptors:
        descriptor_path = f"{fd_directory}/{descriptor}"

        try:
            target = os.readlink(descriptor_path)

            if "(deleted)" not in target:
                continue

            size = os.stat(descriptor_path).st_size

            if size <= 0:
                continue

            records.append(
                (
                    size,
                    pid,
                    descriptor,
                    target,
                    command,
                )
            )
        except OSError:
            continue

if not records:
    print("No deleted files are being held open.")
else:
    print("Deleted files still being held open:")
    print()

    for size, pid, descriptor, target, command in sorted(
        records,
        reverse=True,
    ):
        print(
            f"{size / 1024 / 1024:10.1f} MB  "
            f"PID={pid:<8} FD={descriptor:<5} {target}"
        )
        print(f"             CMD={command}")
PY

echo
echo "============================================================"
echo "CLEANUP COMPLETED"
echo "============================================================"

echo
echo "Moved backups:"
du -sh "$DEST" 2>/dev/null || true

echo
echo "Root filesystem:"
df -h /

echo
echo "EMS storage:"
df -h "$EMS_MOUNT"

echo
echo "Scheduler:"
systemctl is-active bess-seven-day-backend-v3.service || true

echo
echo "Backend:"
systemctl is-active kinetics-gateway.service || true

echo
echo "Backup files were preserved at:"
echo "$DEST"

echo "============================================================"
