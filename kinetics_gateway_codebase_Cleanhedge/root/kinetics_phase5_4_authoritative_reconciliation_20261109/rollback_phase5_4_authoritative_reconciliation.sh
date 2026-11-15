#!/bin/bash
set -euo pipefail
ARCHIVE="${1:-}"
[[ -n "$ARCHIVE" && -f "$ARCHIVE" ]] || { echo "Usage: $0 /root/kinetics_phase5_4_authoritative_reconciliation_backup_TIMESTAMP.tar.gz"; exit 2; }
systemctl stop kinetics-gateway.service
tar -xzf "$ARCHIVE" -C /
systemctl restart kinetics-gateway.service
echo "Rollback restored from $ARCHIVE"
