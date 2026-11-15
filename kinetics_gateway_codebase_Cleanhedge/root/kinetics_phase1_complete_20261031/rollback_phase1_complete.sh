#!/bin/bash
set -Eeuo pipefail

GATEWAY_SERVICE=kinetics-gateway.service
SCHEDULER_SERVICE=bess-seven-day-v302.service
BACKUP=${1:-$(cat /root/kinetics_phase1_complete_latest_backup.txt)}
SCHEDULER_WAS_ACTIVE=$(cat /root/kinetics_phase1_complete_scheduler_was_active.txt 2>/dev/null || echo no)

if [ ! -f "$BACKUP" ]; then
  echo "Backup not found: $BACKUP"
  exit 2
fi

if systemctl is-active --quiet "$SCHEDULER_SERVICE"; then
  systemctl stop "$SCHEDULER_SERVICE"
fi
systemctl stop "$GATEWAY_SERVICE"
tar -xzf "$BACKUP" -C /
systemctl daemon-reload
systemctl start "$GATEWAY_SERVICE"
sleep 5
curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:8000/api/health >/dev/null
if [ "$SCHEDULER_WAS_ACTIVE" = yes ]; then
  systemctl start "$SCHEDULER_SERVICE"
fi
echo "Rollback completed from: $BACKUP"
