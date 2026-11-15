#!/bin/bash
set -euo pipefail
BACKUP_DIR="${1:-$(cat /opt/kinetics-gateway/backups/phase5_8_latest)}"
[[ "${CONFIRM_IDLE:-}" == "YES" ]] || { echo "Use CONFIRM_IDLE=YES only after Safe Stop All."; exit 2; }
[[ -d "$BACKUP_DIR" ]] || { echo "Backup not found: $BACKUP_DIR"; exit 3; }
systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
cp -a "$BACKUP_DIR/backend/." /opt/kinetics-gateway/backend/
[[ ! -f "$BACKUP_DIR/root/bess_solar_time_automation.py" ]] || cp -a "$BACKUP_DIR/root/bess_solar_time_automation.py" /root/
[[ ! -f "$BACKUP_DIR/systemd/bess-seven-day-v302.service" ]] || cp -a "$BACKUP_DIR/systemd/bess-seven-day-v302.service" /etc/systemd/system/
systemctl daemon-reload
systemctl start kinetics-gateway.service
echo "Phase 5.8 rolled back. Scheduler remains stopped for explicit operator review."
