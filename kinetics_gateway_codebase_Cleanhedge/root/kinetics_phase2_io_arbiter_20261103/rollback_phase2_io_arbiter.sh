#!/bin/sh
set -eu

BACKUP=${1:-}
if [ -z "$BACKUP" ] || [ ! -f "$BACKUP" ]; then
    echo "Usage: $0 /root/kinetics_phase2_io_arbiter_backup_TIMESTAMP.tar.gz"
    exit 2
fi
systemctl stop bess-seven-day-v302.service
systemctl stop kinetics-gateway.service
tar -xzf "$BACKUP" -C /
rm -f /opt/kinetics-gateway/backend/app/protocols/io_arbiter.py
rm -f /opt/kinetics-gateway/backend/tests/test_io_arbiter_phase2.py
systemctl restart kinetics-gateway.service
echo "Phase 2 rolled back. Scheduler remains stopped."
