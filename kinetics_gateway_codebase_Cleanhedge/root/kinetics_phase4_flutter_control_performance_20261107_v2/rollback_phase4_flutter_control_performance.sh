#!/bin/bash
set -euo pipefail

ARCHIVE=${1:-}
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "Usage: $0 /root/kinetics_phase4_flutter_control_performance_backup_TIMESTAMP.tar.gz" >&2
  exit 2
fi
if [ "${CONFIRM_IDLE:-}" != YES ]; then
  echo "Refusing rollback: run with CONFIRM_IDLE=YES after confirming the plant is safely stopped." >&2
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
echo "Phase 4 source rollback completed. The additive storage_counters table was retained and is harmless to the previous code."
