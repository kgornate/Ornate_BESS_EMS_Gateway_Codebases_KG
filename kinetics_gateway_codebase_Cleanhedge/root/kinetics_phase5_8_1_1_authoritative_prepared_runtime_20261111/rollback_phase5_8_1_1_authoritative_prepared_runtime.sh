#!/bin/bash
set -euo pipefail
BACKUP_DIR="${1:-$(cat /opt/kinetics-gateway/backups/phase5_8_1_1_latest)}"
[[ "${CONFIRM_IDLE:-}" == "YES" ]] || {
  echo "Use CONFIRM_IDLE=YES only after automation is disabled and all pairs are safely stopped."
  exit 2
}
[[ -f "$BACKUP_DIR/root/bess_solar_time_automation.py" ]] || {
  echo "Backup scheduler not found: $BACKUP_DIR"
  exit 3
}
systemctl stop bess-seven-day-v302.service 2>/dev/null || true
cp -a "$BACKUP_DIR/root/bess_solar_time_automation.py" /root/
chmod 755 /root/bess_solar_time_automation.py
python3 -m py_compile /root/bess_solar_time_automation.py
echo "Rolled back the scheduler. Automation service remains stopped for operator review."
