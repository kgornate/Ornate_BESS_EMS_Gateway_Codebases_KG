#!/bin/bash
set -euo pipefail
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$SOURCE"
sha256sum -c SHA256SUMS.txt
install -d -m 755 /opt/kalpa-upload-dashboard
if [ -f /opt/kalpa-upload-dashboard/dashboard.py ] && ! cmp -s dashboard.py /opt/kalpa-upload-dashboard/dashboard.py; then
    cp -a /opt/kalpa-upload-dashboard/dashboard.py "/opt/kalpa-upload-dashboard/dashboard.py.backup-$(date -u +%Y%m%dT%H%M%SZ)"
fi
install -m 644 dashboard.py /opt/kalpa-upload-dashboard/dashboard.py
cat > /usr/local/bin/kalpa-upload-dashboard <<'LAUNCHER'
#!/bin/sh
exec /usr/bin/python3 /opt/kalpa-upload-dashboard/dashboard.py "$@"
LAUNCHER
chmod 755 /usr/local/bin/kalpa-upload-dashboard
printf '%s\n' 'Installed. Run: kalpa-upload-dashboard' 'No gateway services, application code or configuration were changed.'
