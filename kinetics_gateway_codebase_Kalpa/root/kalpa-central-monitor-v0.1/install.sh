#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'Run this on DEBIX as root.'; exit 1; }
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=/opt/ornate-ems-gateway/venv/bin/python
BACKEND=/opt/ornate-ems-gateway/backend
test -x "$PYTHON"
test -f /etc/ornate-ems-gateway/central_sync.json
test -f /etc/ornate-ems-gateway/ornate-ems-gateway.env
cd "$BACKEND"
"$PYTHON" - <<'PY'
import hashlib,json
from pathlib import Path
from app.central_sync.config import CentralSyncConfig
c=CentralSyncConfig.model_validate(json.loads(Path('/etc/ornate-ems-gateway/central_sync.json').read_text()))
p=c.general_assets.s2_policy_manifest_path
assert p and c.general_assets.s2_policy_expected_sha256, 'Frozen S2 manifest settings missing'
assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == c.general_assets.s2_policy_expected_sha256, 'Frozen S2 manifest checksum mismatch'
print('Frozen S2 preflight: PASS. Production config will not be changed.')
PY
mkdir -p /opt/kalpa-central-monitor /var/lib/kalpa-central-monitor
install -m 755 "$SOURCE_DIR/monitor.py" /opt/kalpa-central-monitor/monitor.py
cat > /usr/local/bin/kalpa-sync-monitor <<'LAUNCH'
#!/bin/sh
exec /opt/ornate-ems-gateway/venv/bin/python /opt/kalpa-central-monitor/monitor.py "$@"
LAUNCH
chmod 755 /usr/local/bin/kalpa-sync-monitor
cat > /etc/systemd/system/kalpa-central-generation.service <<'UNIT'
[Unit]
Description=Kalpa local Central Sync generation test (no uploader or commands)
After=network.target ornate-ems-gateway.service
Wants=ornate-ems-gateway.service

[Service]
Type=simple
WorkingDirectory=/opt/ornate-ems-gateway/backend
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/etc/ornate-ems-gateway/ornate-ems-gateway.env
ExecStart=/opt/ornate-ems-gateway/venv/bin/python /opt/kalpa-central-monitor/monitor.py --generate
Restart=on-failure
RestartSec=10
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl restart kalpa-central-generation.service
printf '%s\n' 'Installed. Open dashboard: kalpa-sync-monitor' \
 'Exit dashboard: Ctrl+C (generation continues)' \
 'Stop generation: systemctl stop kalpa-central-generation' \
 'Service is not enabled at boot; start it manually after a reboot.' \
 'Production uploads and D1 remain unchanged. Local test outbox has a 256 MiB nominal cap.'
