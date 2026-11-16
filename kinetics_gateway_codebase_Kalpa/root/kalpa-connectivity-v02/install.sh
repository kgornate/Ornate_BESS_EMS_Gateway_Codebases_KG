#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root'; exit 1; }
[ -f /var/lib/kalpa-setup/token ] || { echo 'Install v0.1 helper first; token not found'; exit 1; }
[ ! -f /var/lib/kalpa-setup/pending-backup ] || { echo 'A Wi-Fi change is pending. Let it confirm or roll back before updating.'; exit 1; }
python3 -m py_compile "$(dirname "$0")/agent.py" "$(dirname "$0")/connectivity.py"
backup="/opt/kalpa-setup-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
cp /opt/kalpa-setup/agent.py "$backup/agent.py"
cp /etc/systemd/system/kalpa-setup.service "$backup/kalpa-setup.service"
if [ -f /opt/kalpa-setup/connectivity.py ]; then cp /opt/kalpa-setup/connectivity.py "$backup/connectivity.py"; fi
systemctl stop kalpa-setup.service
install -m 644 "$(dirname "$0")/agent.py" /opt/kalpa-setup/agent.py
install -m 644 "$(dirname "$0")/connectivity.py" /opt/kalpa-setup/connectivity.py
install -m 644 "$(dirname "$0")/kalpa-setup.service" /etc/systemd/system/kalpa-setup.service
systemctl daemon-reload
systemctl enable --now kalpa-setup.service
printf '\nSetup helper updated. Existing token and Wi-Fi configuration retained.\nBackup: %s\n' "$backup"
systemctl status kalpa-setup.service --no-pager -l
