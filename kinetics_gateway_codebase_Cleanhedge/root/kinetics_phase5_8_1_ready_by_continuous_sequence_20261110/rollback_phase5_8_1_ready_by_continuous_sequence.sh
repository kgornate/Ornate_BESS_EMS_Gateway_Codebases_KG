#!/bin/bash
set -euo pipefail
BACKUP_DIR="${1:-$(cat /opt/kinetics-gateway/backups/phase5_8_1_latest)}"
[[ "${CONFIRM_IDLE:-}" == "YES" ]] || { echo "Use CONFIRM_IDLE=YES only after Safe Stop All."; exit 2; }
systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
cp -a "$BACKUP_DIR/backend/." /opt/kinetics-gateway/backend/
cp -a "$BACKUP_DIR/root/bess_solar_time_automation.py" /root/
cp -a "$BACKUP_DIR/systemd/bess-seven-day-v302.service" /etc/systemd/system/
systemctl daemon-reload
systemctl start kinetics-gateway.service
echo "Rolled back to Phase 5.8. Automation remains stopped for operator review."
