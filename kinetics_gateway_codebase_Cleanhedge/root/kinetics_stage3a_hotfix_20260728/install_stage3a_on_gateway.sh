#!/bin/sh
set -eu

BACKEND=/opt/kinetics-gateway/backend
CONFIG=/etc/kinetics-gateway/config.json
SERVICE=kinetics-gateway.service
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_stage3a_backup_${STAMP}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run this installer as root" >&2
    exit 1
fi

for path in \
    "$BACKEND/app/core/config.py" \
    "$BACKEND/app/storage/sqlite_store.py" \
    "$BACKEND/app/services/gateway_service.py" \
    "$CONFIG"; do
    if [ ! -f "$path" ]; then
        echo "ERROR: required file missing: $path" >&2
        exit 1
    fi
done

if [ ! -f "$SCRIPT_DIR/files/app/core/config.py" ] || \
   [ ! -f "$SCRIPT_DIR/files/app/storage/sqlite_store.py" ] || \
   [ ! -f "$SCRIPT_DIR/files/app/services/gateway_service.py" ]; then
    echo "ERROR: patch files are missing from $SCRIPT_DIR/files" >&2
    exit 1
fi

mkdir -p "$BACKUP/app/core" "$BACKUP/app/storage" "$BACKUP/app/services" "$BACKUP/etc"
cp -a "$BACKEND/app/core/config.py" "$BACKUP/app/core/config.py"
cp -a "$BACKEND/app/storage/sqlite_store.py" "$BACKUP/app/storage/sqlite_store.py"
cp -a "$BACKEND/app/services/gateway_service.py" "$BACKUP/app/services/gateway_service.py"
cp -a "$CONFIG" "$BACKUP/etc/config.json"
systemctl cat "$SERVICE" > "$BACKUP/kinetics-gateway.service.txt"

# Preserve any old fallback database before starting a clean no-SD baseline.
mkdir -p "$BACKUP/old_fallback_data"
for item in \
    "$BACKEND/data/kinetics_gateway.db" \
    "$BACKEND/data/kinetics_gateway.db-wal" \
    "$BACKEND/data/kinetics_gateway.db-shm" \
    "$BACKEND/data/kinetics_gateway.log"; do
    if [ -e "$item" ]; then
        cp -a "$item" "$BACKUP/old_fallback_data/"
    fi
done

cat > "$BACKUP/rollback.sh" <<'ROLLBACK'
#!/bin/sh
set -eu
BACKEND=/opt/kinetics-gateway/backend
CONFIG=/etc/kinetics-gateway/config.json
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
systemctl stop kinetics-gateway.service
cp -a "$HERE/app/core/config.py" "$BACKEND/app/core/config.py"
cp -a "$HERE/app/storage/sqlite_store.py" "$BACKEND/app/storage/sqlite_store.py"
cp -a "$HERE/app/services/gateway_service.py" "$BACKEND/app/services/gateway_service.py"
cp -a "$HERE/etc/config.json" "$CONFIG"
find "$BACKEND/app" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
systemctl start kinetics-gateway.service
sleep 15
systemctl status kinetics-gateway.service --no-pager -l
ROLLBACK
chmod 700 "$BACKUP/rollback.sh"

echo "Backup created at: $BACKUP"
echo "Stopping $SERVICE ..."
systemctl stop "$SERVICE"

cp -a "$SCRIPT_DIR/files/app/core/config.py" "$BACKEND/app/core/config.py"
cp -a "$SCRIPT_DIR/files/app/storage/sqlite_store.py" "$BACKEND/app/storage/sqlite_store.py"
cp -a "$SCRIPT_DIR/files/app/services/gateway_service.py" "$BACKEND/app/services/gateway_service.py"

python3 - "$CONFIG" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
storage = data.setdefault("storage", {})
storage.update(
    {
        "fallback_sample_interval_seconds": 30.0,
        "fallback_telemetry_retention_days": 3,
        "fallback_quota_gb": 0.5,
        "update_buffer_events": 200,
        "status_refresh_seconds": 60.0,
        "compression_level": 1,
    }
)
tmp = path.with_suffix(path.suffix + ".stage3a.tmp")
tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
tmp.replace(path)
PY

# Start with a clean bounded fallback DB while the SD card is absent.
if ! mountpoint -q /mnt/ems-logs; then
    rm -f \
        "$BACKEND/data/kinetics_gateway.db" \
        "$BACKEND/data/kinetics_gateway.db-wal" \
        "$BACKEND/data/kinetics_gateway.db-shm" \
        "$BACKEND/data/kinetics_gateway.log"
fi

find "$BACKEND/app" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

/opt/kinetics-gateway/venv/bin/python3 -m py_compile \
    "$BACKEND/app/core/config.py" \
    "$BACKEND/app/storage/sqlite_store.py" \
    "$BACKEND/app/services/gateway_service.py"

# Validate that the real deployed configuration still parses before startup.
KINETICS_CONFIG="$CONFIG" \
PYTHONPATH="$BACKEND" \
/opt/kinetics-gateway/venv/bin/python3 - <<'PY'
from app.core.config import load_config
config = load_config()
print("Config validated")
print("fallback_sample_interval_seconds=", config.storage.fallback_sample_interval_seconds)
print("fallback_quota_gb=", config.storage.fallback_quota_gb)
print("update_buffer_events=", config.storage.update_buffer_events)
PY

echo "Starting $SERVICE ..."
systemctl start "$SERVICE"
sleep 20

if [ "$(systemctl is-active "$SERVICE")" != "active" ]; then
    echo "ERROR: gateway did not become active. Rolling back." >&2
    sh "$BACKUP/rollback.sh"
    exit 1
fi

HEALTH=/tmp/kinetics_stage3a_health.json
if ! curl -sS --connect-timeout 5 --max-time 30 \
    http://127.0.0.1:8000/api/health -o "$HEALTH"; then
    echo "ERROR: health endpoint failed. Rolling back." >&2
    sh "$BACKUP/rollback.sh"
    exit 1
fi

python3 - "$HEALTH" <<'PY'
import json
import sys
health = json.load(open(sys.argv[1], encoding="utf-8"))
storage = health.get("storage", {})
print("health_status=", health.get("status"))
print("storage_mode=", storage.get("storage_mode"))
print("storage_root=", storage.get("root"))
print("preferred_mount_ready=", storage.get("preferred_mount_ready"))
print("effective_sample_interval_seconds=", storage.get("effective_sample_interval_seconds"))
print("effective_retention_days=", storage.get("effective_retention_days"))
print("quota_bytes=", storage.get("quota_bytes"))
if storage.get("preferred_mount_ready") is False:
    assert storage.get("storage_mode") == "fallback_rootfs"
    assert float(storage.get("effective_sample_interval_seconds")) >= 30.0
PY

PID=$(systemctl show "$SERVICE" -p MainPID --value)
printf '\nService after Stage 3A:\n'
systemctl show "$SERVICE" -p MainPID -p NRestarts -p MemoryCurrent -p MemoryPeak -p ActiveEnterTimestamp
ps -p "$PID" -o pid,etime,%cpu,%mem,rss,vsz,cmd
printf '\nRollback command if required:\n%s\n' "$BACKUP/rollback.sh"
printf '\nStage 3A installation completed successfully.\n'
