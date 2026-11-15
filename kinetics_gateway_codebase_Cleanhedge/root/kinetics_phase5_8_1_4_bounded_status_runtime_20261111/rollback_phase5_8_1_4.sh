#!/bin/bash
set -euo pipefail
BACKUP_DIR="$(cat /opt/kinetics-gateway/backups/phase5_8_1_4_latest)"
[[ -d "$BACKUP_DIR" ]] || { echo "Backup not found: $BACKUP_DIR"; exit 2; }
systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
cp -a "$BACKUP_DIR/backend/app/main.py" /opt/kinetics-gateway/backend/app/
cp -a "$BACKUP_DIR/backend/app/services/bms_pcs_control.py" /opt/kinetics-gateway/backend/app/services/
cp -a "$BACKUP_DIR/backend/app/services/gateway_service.py" /opt/kinetics-gateway/backend/app/services/
cp -a "$BACKUP_DIR/root/bess_solar_time_automation.py" /root/
systemctl start kinetics-gateway.service
systemctl start bess-seven-day-v302.service
echo "Rolled back Phase 5.8.1.4 from $BACKUP_DIR"
