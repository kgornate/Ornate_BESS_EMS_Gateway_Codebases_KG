#!/bin/bash
set -euo pipefail
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd)
install -d -m 755 /opt/kalpa-sync-dashboard
install -m 644 "$SOURCE/dashboard.py" /opt/kalpa-sync-dashboard/dashboard.py
cat > /usr/local/bin/kalpa-sync-dashboard <<'SH'
#!/bin/sh
exec /usr/bin/python3 /opt/kalpa-sync-dashboard/dashboard.py "$@"
SH
chmod 755 /usr/local/bin/kalpa-sync-dashboard
echo 'Installed. Run: kalpa-sync-dashboard'
echo 'No application services or configurations have been changed.'
