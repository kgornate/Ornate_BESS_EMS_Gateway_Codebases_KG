#!/bin/bash
set -euo pipefail

BACKUP=${1:?Usage: $0 /root/kinetics_phase3c_abb_grid_backup_TIMESTAMP.tar.gz}

systemctl stop kinetics-gateway.service
tar -xzf "$BACKUP" -C /
rm -f /opt/kinetics-gateway/backend/tests/test_solar_grid_meter_phase3c.py
systemctl daemon-reload
systemctl restart kinetics-gateway.service

echo "Phase 3C ABB grid-meter rollback complete"
