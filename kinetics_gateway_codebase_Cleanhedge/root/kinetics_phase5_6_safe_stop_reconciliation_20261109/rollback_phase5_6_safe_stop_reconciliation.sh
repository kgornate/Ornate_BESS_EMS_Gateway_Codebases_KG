#!/bin/bash
set -euo pipefail
BACKUP="${1:-}"
[[ -n "$BACKUP" && -f "$BACKUP" ]] || {
  echo "Usage: $0 /root/kinetics_phase5_6_safe_stop_reconciliation_backup_<timestamp>.tar.gz"
  exit 2
}
systemctl stop bess-seven-day-v302.service 2>/dev/null || true
systemctl stop kinetics-gateway.service
tar -xzf "$BACKUP" -C /
systemctl restart kinetics-gateway.service
echo "Phase 5.6 rollback restored from: $BACKUP"
