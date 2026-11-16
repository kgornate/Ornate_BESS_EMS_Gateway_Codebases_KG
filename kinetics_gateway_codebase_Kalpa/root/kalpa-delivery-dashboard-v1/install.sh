#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
sha256sum -c SHA256SUMS.txt
install -d -m755 /opt/kalpa-delivery-dashboard
install -m644 dashboard.py metrics.py /opt/kalpa-delivery-dashboard/
cat > /usr/local/bin/kalpa-delivery-dashboard <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /opt/kalpa-delivery-dashboard/dashboard.py "$@"
EOF
chmod 755 /usr/local/bin/kalpa-delivery-dashboard
echo 'Installed. Run kalpa-delivery-dashboard. No gateway services or configs changed.'
