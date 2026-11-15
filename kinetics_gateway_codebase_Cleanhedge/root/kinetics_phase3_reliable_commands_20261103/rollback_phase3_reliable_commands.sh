#!/bin/sh
set -eu

BACKUP=${1:-}
if [ -z "$BACKUP" ] || [ ! -f "$BACKUP" ]; then
    echo "Usage: $0 /root/kinetics_phase3_reliable_commands_backup_TIMESTAMP.tar.gz"
    exit 2
fi
systemctl stop bess-seven-day-v302.service
systemctl stop kinetics-gateway.service
tar -xzf "$BACKUP" -C /
rm -f /opt/kinetics-gateway/backend/app/protocols/command_reliability.py
rm -f /opt/kinetics-gateway/backend/tests/test_command_reliability_phase3.py
systemctl restart kinetics-gateway.service
echo "Phase 3 rolled back to Phase 2. Scheduler remains stopped."
