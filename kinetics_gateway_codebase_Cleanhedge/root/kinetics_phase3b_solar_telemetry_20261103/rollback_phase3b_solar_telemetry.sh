#!/bin/bash
set -euo pipefail
BACKUP=${1:?Usage: $0 /root/kinetics_phase3b_solar_backup_TIMESTAMP.tar.gz}
systemctl stop kinetics-gateway.service
tar -xzf "$BACKUP" -C /
rm -f /opt/kinetics-gateway/backend/app/assets/solar_driver.py \
      /opt/kinetics-gateway/backend/tests/test_solar_phase3b.py
systemctl daemon-reload
systemctl restart kinetics-gateway.service
echo "Phase 3B rollback complete"
