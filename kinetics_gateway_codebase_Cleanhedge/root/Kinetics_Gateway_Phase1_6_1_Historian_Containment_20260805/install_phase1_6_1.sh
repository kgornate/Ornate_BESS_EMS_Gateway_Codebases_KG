#!/bin/sh
set -eu

TARGET=/opt/kinetics-gateway/backend
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/kinetics_gateway_phase1_6_1_backup_$STAMP

test -d "$TARGET/app"
mkdir -p "$BACKUP/app/api" "$BACKUP/app/core" "$BACKUP/app/services" "$BACKUP/tests"
cp -a "$TARGET/app/api/routes.py" "$BACKUP/app/api/routes.py"
cp -a "$TARGET/app/core/config.py" "$BACKUP/app/core/config.py"
test ! -f "$TARGET/app/services/historian_admission.py" || cp -a "$TARGET/app/services/historian_admission.py" "$BACKUP/app/services/historian_admission.py"
test ! -f "$TARGET/tests/test_historian_admission.py" || cp -a "$TARGET/tests/test_historian_admission.py" "$BACKUP/tests/test_historian_admission.py"

cp -a payload/app/api/routes.py "$TARGET/app/api/routes.py"
cp -a payload/app/core/config.py "$TARGET/app/core/config.py"
cp -a payload/app/services/historian_admission.py "$TARGET/app/services/historian_admission.py"
cp -a payload/tests/test_historian_admission.py "$TARGET/tests/test_historian_admission.py"

cd "$TARGET"
python3 -m compileall -q app tests
if test -x .venv/bin/python; then
  .venv/bin/python -m pytest -q tests/test_historian_admission.py tests/test_http_admission.py
else
  python3 -m pytest -q tests/test_historian_admission.py tests/test_http_admission.py
fi

systemctl restart kinetics-gateway.service
systemctl is-active --quiet kinetics-gateway.service
echo "Installed Phase 1.6.1; backup: $BACKUP"
