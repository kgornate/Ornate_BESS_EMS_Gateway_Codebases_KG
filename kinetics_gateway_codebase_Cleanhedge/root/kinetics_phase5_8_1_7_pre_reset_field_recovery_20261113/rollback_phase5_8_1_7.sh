#!/bin/bash
set -euo pipefail
[[ "${CONFIRM_IDLE:-}" == "YES" ]] || { echo "Disable automation, Save, verify all pairs stopped, then use CONFIRM_IDLE=YES."; exit 2; }
BACKUP_DIR="$(cat /opt/kinetics-gateway/backups/phase5_8_1_7_latest)"
[[ -d "$BACKUP_DIR" ]] || { echo "Backup not found: $BACKUP_DIR"; exit 2; }
python3 - <<'PY'
import json
from pathlib import Path
config = json.loads(Path("/mnt/ems-logs/kinetics-gateway/automation_schedule.json").read_text())
if config.get("enabled"): raise SystemExit("Automation remains enabled. Disable it and Save first.")
PY
systemctl stop bess-seven-day-v302.service
cp -a "$BACKUP_DIR/root/bess_solar_time_automation.py" /root/
rm -f /opt/kinetics-gateway/backend/tests/test_phase5_8_1_7_pre_reset_recovery.py
systemctl start bess-seven-day-v302.service
echo "Rolled back Phase 5.8.1.7 from $BACKUP_DIR"
