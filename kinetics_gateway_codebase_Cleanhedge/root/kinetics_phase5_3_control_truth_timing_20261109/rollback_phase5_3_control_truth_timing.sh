#!/bin/bash
set -euo pipefail
ARCHIVE="${1:?Usage: $0 /root/kinetics_phase5_3_control_truth_timing_backup_TIMESTAMP.tar.gz}"
systemctl stop kinetics-gateway.service
tar -xzf "$ARCHIVE" -C /
systemctl restart kinetics-gateway.service
echo "Restored $ARCHIVE"

