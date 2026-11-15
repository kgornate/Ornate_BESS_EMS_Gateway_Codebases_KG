#!/bin/bash
set -euo pipefail
ARCHIVE="${1:-}"
[[ -f "$ARCHIVE" ]] || { echo "Usage: $0 /root/kinetics_phase5_2_control_reliability_backup_TIMESTAMP.tar.gz"; exit 2; }
SCHEDULER_WAS_ACTIVE=no
if systemctl is-active --quiet bess-seven-day-v302.service; then SCHEDULER_WAS_ACTIVE=yes; systemctl stop bess-seven-day-v302.service; fi
systemctl stop kinetics-gateway.service
tar -xzf "$ARCHIVE" -C /
systemctl restart kinetics-gateway.service
[[ "$SCHEDULER_WAS_ACTIVE" == no ]] || systemctl restart bess-seven-day-v302.service
echo "Rollback completed from $ARCHIVE"
