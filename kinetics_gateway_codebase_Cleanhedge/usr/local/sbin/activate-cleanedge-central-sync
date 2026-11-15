#!/bin/sh
set -eu

CONFIG=/etc/kinetics-gateway/central_sync.json
ENV_FILE=/etc/kinetics-gateway/central_sync.env
PYTHON=/opt/kinetics-gateway/venv/bin/python
BACKUP="$CONFIG.before-enable-$(date -u +%Y%m%dT%H%M%SZ)"

test -f "$CONFIG"
test -f "$ENV_FILE"
grep -Eq '^CENTRAL_SYNC_GATEWAY_CLIENT_ID=.+$' "$ENV_FILE"
grep -Eq '^CENTRAL_SYNC_GATEWAY_CLIENT_SECRET=.+$' "$ENV_FILE"
cp -a "$CONFIG" "$BACKUP"
"$PYTHON" - "$CONFIG" <<'PY'
import json, os, sys, tempfile
path=sys.argv[1]
data=json.load(open(path,encoding='utf-8'))
assert data['identity']['site_id']=='cleanedge'
assert data['identity']['gateway_id']=='kinetics_gateway_1'
assert data['command_downlink']['enabled'] is False
data['enabled']=True
fd,tmp=tempfile.mkstemp(prefix='central_sync.json.',dir=os.path.dirname(path),text=True)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as handle:
        json.dump(data,handle,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
    os.chmod(tmp,0o600);os.replace(tmp,path)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
PY
systemctl enable --now kinetics-central-sync.service
systemctl --no-pager --full status kinetics-central-sync.service
echo "Central Sync enabled. Previous config: $BACKUP"
