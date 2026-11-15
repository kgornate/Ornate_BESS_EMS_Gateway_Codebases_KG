#!/bin/bash
set -euo pipefail
[[ "${CONFIRM_IDLE:-}" == YES ]] || {
  echo "Set CONFIRM_IDLE=YES only after all pairs are safely stopped."
  exit 2
}
[[ $# -eq 1 ]] || { echo "Usage: CONFIRM_IDLE=YES $0 /root/kinetics_phase5_7_1_control_truth_safe_stop_progress_backup_<timestamp>.tar.gz"; exit 2; }
BACKUP="$1"
[[ -f "$BACKUP" ]] || { echo "Backup not found: $BACKUP"; exit 2; }
systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
tar -xzf "$BACKUP" -C /
/opt/kinetics-gateway/venv/bin/python -m compileall -q /opt/kinetics-gateway/backend/app
systemctl restart kinetics-gateway.service
echo "Phase 5.7.1 rollback restored from $BACKUP"

