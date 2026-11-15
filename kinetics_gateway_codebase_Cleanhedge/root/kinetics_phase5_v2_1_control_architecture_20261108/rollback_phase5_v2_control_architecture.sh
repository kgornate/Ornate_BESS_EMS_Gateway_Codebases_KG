#!/bin/bash
set -euo pipefail

ARCHIVE=${1:-}
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "Usage: CONFIRM_IDLE=YES $0 /root/kinetics_phase5_v2_control_architecture_backup_TIMESTAMP.tar.gz" >&2
  exit 2
fi
if [ "${CONFIRM_IDLE:-}" != YES ]; then
  echo "Refusing rollback until all pairs are safely stopped and CONFIRM_IDLE=YES is supplied." >&2
  exit 3
fi
if systemctl is-active --quiet bess-seven-day-v302.service; then
  echo "Refusing rollback: scheduler must be inactive." >&2
  exit 4
fi

systemctl stop kinetics-gateway.service
tar -xzf "$ARCHIVE" -C /
systemctl restart kinetics-gateway.service
for _ in $(seq 1 36); do
  curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 5
done
curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null
echo "Phase 5 v2 rollback completed."
