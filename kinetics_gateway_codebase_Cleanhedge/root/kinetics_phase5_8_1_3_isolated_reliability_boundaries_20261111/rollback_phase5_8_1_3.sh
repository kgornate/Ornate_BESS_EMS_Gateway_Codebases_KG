#!/bin/bash
set -euo pipefail
BACKUP_FILE="/opt/kinetics-gateway/backups/phase5_8_1_3_latest"
[[ -f "$BACKUP_FILE" ]] || { echo "Missing $BACKUP_FILE"; exit 2; }
BACKUP_DIR="$(cat "$BACKUP_FILE")"
[[ -d "$BACKUP_DIR" ]] || { echo "Missing backup directory: $BACKUP_DIR"; exit 2; }
[[ "${CONFIRM_IDLE:-}" == "YES" ]] || {
  echo "Verify all pairs stopped, then use CONFIRM_IDLE=YES."
  exit 2
}
systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
cp -a "$BACKUP_DIR/backend/app/main.py" /opt/kinetics-gateway/backend/app/
cp -a "$BACKUP_DIR/backend/app/api/routes.py" /opt/kinetics-gateway/backend/app/api/
cp -a "$BACKUP_DIR/backend/app/services/." /opt/kinetics-gateway/backend/app/services/
cp -a "$BACKUP_DIR/backend/tests/." /opt/kinetics-gateway/backend/tests/ 2>/dev/null || true
cp -a "$BACKUP_DIR/root/bess_solar_time_automation.py" /root/
systemctl start kinetics-gateway.service
systemctl start bess-seven-day-v302.service
echo "Rolled back Phase 5.8.1.3 from $BACKUP_DIR. Automation configuration was not changed."
