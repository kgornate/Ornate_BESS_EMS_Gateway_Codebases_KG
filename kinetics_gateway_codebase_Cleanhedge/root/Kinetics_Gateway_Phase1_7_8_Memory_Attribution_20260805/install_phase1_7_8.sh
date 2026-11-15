#!/bin/sh
set -eu

TARGET=/opt/kinetics-gateway/backend
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_gateway_phase1_7_8_backup_$STAMP

test -d "$TARGET/app"
test "$(systemctl is-enabled bess-seven-day-v302.service 2>/dev/null || true)" != "enabled"

mkdir -p "$BACKUP/app/storage" "$BACKUP/app/services" "$BACKUP/app" "$BACKUP/tools" "$BACKUP/tests"
cp -a "$TARGET/app/storage/sqlite_store.py" "$BACKUP/app/storage/sqlite_store.py"
cp -a "$TARGET/app/services/runtime_metrics.py" "$BACKUP/app/services/runtime_metrics.py"
cp -a "$TARGET/app/main.py" "$BACKUP/app/main.py"
test ! -f "$TARGET/tools/phase1_memory_probe.py" || cp -a "$TARGET/tools/phase1_memory_probe.py" "$BACKUP/tools/phase1_memory_probe.py"
test ! -f "$TARGET/tests/test_phase1_7_8_runtime.py" || cp -a "$TARGET/tests/test_phase1_7_8_runtime.py" "$BACKUP/tests/test_phase1_7_8_runtime.py"

cp -a payload/app/storage/sqlite_store.py "$TARGET/app/storage/sqlite_store.py"
cp -a payload/app/services/runtime_metrics.py "$TARGET/app/services/runtime_metrics.py"
cp -a payload/app/main.py "$TARGET/app/main.py"
cp -a payload/tools/phase1_memory_probe.py "$TARGET/tools/phase1_memory_probe.py"
cp -a payload/tests/test_phase1_7_8_runtime.py "$TARGET/tests/test_phase1_7_8_runtime.py"
chmod 0755 "$TARGET/tools/phase1_memory_probe.py"

cd "$TARGET"
python3 -m compileall -q app tools tests
if test -x .venv/bin/python; then
  .venv/bin/python -m pytest -q tests/test_phase1_7_8_runtime.py tests/test_runtime_metrics.py
else
  python3 -m pytest -q tests/test_phase1_7_8_runtime.py tests/test_runtime_metrics.py
fi

systemctl restart kinetics-gateway.service
systemctl is-active --quiet kinetics-gateway.service
echo "Installed Phase 1.7+1.8; backup: $BACKUP"

