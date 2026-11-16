#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root'; exit 1; }
for tool in python3 wpa_cli iw ip systemctl; do
    command -v "$tool" >/dev/null || { echo "Missing: $tool"; exit 1; }
done
[ -f /etc/wpa_supplicant/mlan0.conf ] || { echo 'Expected mlan0 WPA configuration missing'; exit 1; }
for service in debix-wifi.service debix-wifi-dhcp.service; do
    systemctl cat "$service" >/dev/null || exit 1
done
ip link show eth0 >/dev/null
install -d -m 755 /opt/kalpa-setup
install -d -m 700 /var/lib/kalpa-setup
install -m 644 "$(dirname "$0")/agent.py" /opt/kalpa-setup/agent.py
if [ ! -f /var/lib/kalpa-setup/token ]; then
    python3 - <<'PY'
import os, secrets
fd = os.open('/var/lib/kalpa-setup/token', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f: f.write(secrets.token_urlsafe(32) + '\n')
PY
fi
install -m 644 "$(dirname "$0")/kalpa-setup.service" /etc/systemd/system/kalpa-setup.service
systemctl daemon-reload
systemctl enable --now kalpa-setup.service
systemctl restart kalpa-setup.service
printf '\nSetup token (enter in desktop app; keep private):\n'
cat /var/lib/kalpa-setup/token
