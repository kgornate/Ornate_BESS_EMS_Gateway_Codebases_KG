#!/bin/sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
INSTALL_ROOT=/opt/kinetics-gateway/backend
CONFIG_ROOT=/etc/kinetics-gateway
STATE_ROOT=/var/lib/kinetics-central-sync
ARCHIVE_ROOT=/mnt/ems-logs/kinetics-gateway/central_sync/backlog_archives
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_ROOT=/root/cleanedge-central-sync-backup-$STAMP

test -d "$INSTALL_ROOT/app"
test -x /opt/kinetics-gateway/venv/bin/python
test -f "$CONFIG_ROOT/config.json"

mkdir -p "$BACKUP_ROOT/backend/app" "$BACKUP_ROOT/backend/configs" "$BACKUP_ROOT/systemd"
[ ! -e "$INSTALL_ROOT/app/central_sync" ] || cp -a "$INSTALL_ROOT/app/central_sync" "$BACKUP_ROOT/backend/app/"
for name in central_sync.json archive_transport.json archive_recovery_budget.json recovery_policy.json central_sync.env; do
  [ ! -e "$CONFIG_ROOT/$name" ] || cp -a "$CONFIG_ROOT/$name" "$BACKUP_ROOT/$name"
done
[ ! -e /etc/systemd/system/kinetics-central-sync.service ] || \
  cp -a /etc/systemd/system/kinetics-central-sync.service "$BACKUP_ROOT/systemd/"

install -d -m 0755 "$INSTALL_ROOT/app/central_sync" "$INSTALL_ROOT/delivery_dashboard" "$INSTALL_ROOT/tools" "$INSTALL_ROOT/tools/continuous_observer" "$INSTALL_ROOT/validation"
cp -a "$PACKAGE_ROOT/app/central_sync/." "$INSTALL_ROOT/app/central_sync/"
cp -a "$PACKAGE_ROOT/delivery_dashboard/." "$INSTALL_ROOT/delivery_dashboard/"
install -m 0755 "$PACKAGE_ROOT/tools/build_cleanedge_s2_manifest.py" "$INSTALL_ROOT/tools/build_cleanedge_s2_manifest.py"
cp -a "$PACKAGE_ROOT/tools/continuous_observer/." "$INSTALL_ROOT/tools/continuous_observer/"
cp -a "$PACKAGE_ROOT/validation/." "$INSTALL_ROOT/validation/"
install -m 0644 "$PACKAGE_ROOT/configs/cleanedge_s2_policy_frozen_v1_0.json" "$INSTALL_ROOT/configs/cleanedge_s2_policy_frozen_v1_0.json"
install -m 0644 "$PACKAGE_ROOT/configs/central_sync_cleanedge_v1_0.json" "$CONFIG_ROOT/central_sync.json"
install -m 0644 "$PACKAGE_ROOT/configs/archive_transport.json" "$CONFIG_ROOT/archive_transport.json"
install -m 0644 "$PACKAGE_ROOT/configs/archive_recovery_budget.json" "$CONFIG_ROOT/archive_recovery_budget.json"
install -m 0644 "$PACKAGE_ROOT/configs/recovery_policy.json" "$CONFIG_ROOT/recovery_policy.json"
[ -e "$CONFIG_ROOT/central_sync.env" ] || install -m 0600 "$PACKAGE_ROOT/configs/central_sync.env.example" "$CONFIG_ROOT/central_sync.env"
install -d -m 0700 "$STATE_ROOT"
install -d -m 0750 "$ARCHIVE_ROOT"
install -d -m 0755 /usr/local/bin /usr/local/sbin
install -m 0644 "$PACKAGE_ROOT/deployment/kinetics-central-sync.service" /etc/systemd/system/kinetics-central-sync.service
install -m 0755 "$PACKAGE_ROOT/deployment/kinetics-delivery-dashboard" /usr/local/bin/kinetics-delivery-dashboard
install -m 0755 "$PACKAGE_ROOT/deployment/activate_cleanedge_central_sync.sh" /usr/local/sbin/activate-cleanedge-central-sync
install -m 0755 "$PACKAGE_ROOT/deployment/disable_cleanedge_central_sync.sh" /usr/local/sbin/disable-cleanedge-central-sync

/opt/kinetics-gateway/venv/bin/pip install 'httpx>=0.27,<1'
cd "$INSTALL_ROOT"
/opt/kinetics-gateway/venv/bin/python -m compileall -q app/central_sync delivery_dashboard
/opt/kinetics-gateway/venv/bin/python - <<'PY'
from app.central_sync.config import load_central_sync_config
from app.central_sync.s2_policy import S2PolicyManifest
c=load_central_sync_config('/etc/kinetics-gateway/central_sync.json')
assert not c.command_downlink.enabled
S2PolicyManifest(c.general_assets.s2_policy_manifest_path,
                 expected_sha256=c.general_assets.s2_policy_expected_sha256)
print('CleanEdge Central Sync configuration and frozen S2 manifest validated')
PY
systemctl daemon-reload
systemctl disable kinetics-central-sync.service >/dev/null 2>&1 || true

printf '%s\n' "$BACKUP_ROOT" > /root/cleanedge-central-sync-last-backup
echo "Installed safely with Central Sync disabled. Backup: $BACKUP_ROOT"
if [ "${ENABLE_CENTRAL_SYNC:-NO}" = YES ]; then
  grep -Eq '^CENTRAL_SYNC_GATEWAY_CLIENT_ID=.+$' "$CONFIG_ROOT/central_sync.env" || {
    echo "Refusing enable: client ID is not configured" >&2; exit 1;
  }
  grep -Eq '^CENTRAL_SYNC_GATEWAY_CLIENT_SECRET=.+$' "$CONFIG_ROOT/central_sync.env" || {
    echo "Refusing enable: client secret is not configured" >&2; exit 1;
  }
  /opt/kinetics-gateway/venv/bin/python -c "import json;p='$CONFIG_ROOT/central_sync.json';d=json.load(open(p));assert d['enabled'] is True"
  systemctl enable --now kinetics-central-sync.service
fi
