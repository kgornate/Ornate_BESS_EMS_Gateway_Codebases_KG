#!/bin/sh
set -eu

BACKUP_ROOT=${1:-$(cat /root/cleanedge-central-sync-last-backup 2>/dev/null || true)}
test -n "$BACKUP_ROOT"
test -d "$BACKUP_ROOT"
INSTALL_ROOT=/opt/kinetics-gateway/backend
CONFIG_ROOT=/etc/kinetics-gateway

systemctl disable --now kinetics-central-sync.service >/dev/null 2>&1 || true
if [ -d "$BACKUP_ROOT/backend/app/central_sync" ]; then
  install -d -m 0755 "$INSTALL_ROOT/app/central_sync"
  cp -a "$BACKUP_ROOT/backend/app/central_sync/." "$INSTALL_ROOT/app/central_sync/"
else
  mv "$INSTALL_ROOT/app/central_sync" "$BACKUP_ROOT/installed-central-sync-removed" 2>/dev/null || true
fi
for name in central_sync.json archive_transport.json archive_recovery_budget.json recovery_policy.json central_sync.env; do
  [ ! -e "$BACKUP_ROOT/$name" ] || cp -a "$BACKUP_ROOT/$name" "$CONFIG_ROOT/$name"
done
if [ -f "$BACKUP_ROOT/systemd/kinetics-central-sync.service" ]; then
  cp -a "$BACKUP_ROOT/systemd/kinetics-central-sync.service" /etc/systemd/system/
else
  mv /etc/systemd/system/kinetics-central-sync.service "$BACKUP_ROOT/installed-service-removed" 2>/dev/null || true
fi
systemctl daemon-reload
echo "Rollback restored from $BACKUP_ROOT; CleanEdge polling/control service was not modified."
