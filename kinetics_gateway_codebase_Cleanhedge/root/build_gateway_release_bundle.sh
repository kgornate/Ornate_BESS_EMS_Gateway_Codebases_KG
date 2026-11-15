#!/bin/bash
set -euo pipefail

NAME="kinetics_gateway_latest_release"
BUNDLE="/root/$NAME"
ARCHIVE="/root/$NAME.tar.gz"

echo "Creating structured gateway release bundle..."

rm -rf "$BUNDLE"
rm -f "$ARCHIVE"

mkdir -p \
    "$BUNDLE/diagnostics" \
    "$BUNDLE/etc" \
    "$BUNDLE/opt" \
    "$BUNDLE/patch-history" \
    "$BUNDLE/systemd/service-files" \
    "$BUNDLE/systemd/service-definitions" \
    "$BUNDLE/operational-scripts"

if [ ! -d /opt/kinetics-gateway ]; then
    echo "ERROR: /opt/kinetics-gateway not found"
    exit 1
fi

echo "Copying deployed gateway code..."

tar \
    --exclude='kinetics-gateway/venv' \
    --exclude='kinetics-gateway/venv/*' \
    --exclude='*/__pycache__' \
    --exclude='*/__pycache__/*' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.db-wal' \
    --exclude='*.db-shm' \
    -C /opt \
    -cf - kinetics-gateway |
tar -C "$BUNDLE/opt" -xf -

echo "Copying active configurations..."

if [ -d /etc/kinetics-gateway ]; then
    cp -a /etc/kinetics-gateway "$BUNDLE/etc/"
fi

if [ -d /etc/cloudflared ]; then
    cp -a /etc/cloudflared "$BUNDLE/etc/"
fi

if [ -f /etc/ems_boot_v3.conf ]; then
    cp -a /etc/ems_boot_v3.conf "$BUNDLE/etc/"
fi

echo "Copying operational scripts..."

for FILE in \
    /root/ems_safe_stop_all.sh \
    /root/monitor_all_pairs_cached.sh \
    /root/package_latest_gateway.sh
do
    if [ -f "$FILE" ]; then
        cp -a "$FILE" "$BUNDLE/operational-scripts/"
    fi
done

echo "Capturing service files..."

systemctl list-unit-files \
    --type=service \
    --no-legend |
awk '{print $1}' |
grep -Ei 'kinetics|ems|cloudflared' |
sort -u \
    > "$BUNDLE/systemd/GATEWAY_SERVICE_LIST.txt" || true

while IFS= read -r UNIT; do
    [ -n "$UNIT" ] || continue

    SAFE_NAME=$(printf '%s' "$UNIT" | tr '/@' '__')

    systemctl cat "$UNIT" \
        > "$BUNDLE/systemd/service-definitions/${SAFE_NAME}.txt" \
        2>&1 || true

    systemctl show "$UNIT" \
        -p FragmentPath \
        -p DropInPaths \
        -p ExecStart \
        -p Environment \
        -p ActiveState \
        -p SubState \
        -p MainPID \
        -p NRestarts \
        -p MemoryCurrent \
        -p MemoryPeak \
        > "$BUNDLE/systemd/service-definitions/${SAFE_NAME}_runtime.txt" \
        2>&1 || true

    UNIT_PATH=$(
        systemctl show "$UNIT" \
            -p FragmentPath \
            --value \
            2>/dev/null || true
    )

    if [ -n "$UNIT_PATH" ] && [ -f "$UNIT_PATH" ]; then
        cp -a \
            "$UNIT_PATH" \
            "$BUNDLE/systemd/service-files/${SAFE_NAME}"
    fi
done < "$BUNDLE/systemd/GATEWAY_SERVICE_LIST.txt"

echo "Capturing runtime diagnostics..."

date -Is > "$BUNDLE/diagnostics/captured_at.txt"
hostname > "$BUNDLE/diagnostics/hostname.txt"
uname -a > "$BUNDLE/diagnostics/uname.txt"

if [ -f /etc/os-release ]; then
    cp -a /etc/os-release "$BUNDLE/diagnostics/os-release.txt"
fi

free -h > "$BUNDLE/diagnostics/memory.txt"
df -h > "$BUNDLE/diagnostics/storage.txt"
ip addr show > "$BUNDLE/diagnostics/ip-addresses.txt"
ip route show > "$BUNDLE/diagnostics/ip-routes.txt"
ps -ef > "$BUNDLE/diagnostics/processes.txt"

ss -lntup \
    > "$BUNDLE/diagnostics/listening-ports.txt" \
    2>&1 || true

systemctl status kinetics-gateway.service \
    --no-pager -l \
    > "$BUNDLE/diagnostics/kinetics-gateway-status.txt" \
    2>&1 || true

systemctl show kinetics-gateway.service \
    -p FragmentPath \
    -p ExecStart \
    -p ActiveState \
    -p SubState \
    -p MainPID \
    -p NRestarts \
    -p MemoryCurrent \
    -p MemoryPeak \
    -p ActiveEnterTimestamp \
    > "$BUNDLE/diagnostics/kinetics-gateway-runtime.txt" \
    2>&1 || true

journalctl -u kinetics-gateway.service \
    -n 3000 \
    --no-pager \
    > "$BUNDLE/diagnostics/kinetics-gateway-journal-last-3000.txt" \
    2>&1 || true

journalctl -u cloudflared.service \
    -n 1000 \
    --no-pager \
    > "$BUNDLE/diagnostics/cloudflared-journal-last-1000.txt" \
    2>&1 || true

journalctl -k \
    --no-pager |
grep -Ei 'oom|out of memory|killed process' \
    > "$BUNDLE/diagnostics/kernel-oom-events.txt" \
    2>&1 || true

if [ -x /opt/kinetics-gateway/venv/bin/python ]; then
    /opt/kinetics-gateway/venv/bin/python --version \
        > "$BUNDLE/diagnostics/python-version.txt" \
        2>&1

    /opt/kinetics-gateway/venv/bin/python -m pip freeze \
        > "$BUNDLE/diagnostics/pip-freeze.txt" \
        2>&1
fi

curl -sS \
    --connect-timeout 5 \
    --max-time 20 \
    -w '\nHTTP_STATUS=%{http_code}\n' \
    http://127.0.0.1:8000/api/health \
    > "$BUNDLE/diagnostics/api-health.txt" \
    2>&1 || true

echo "Capturing patch and backup history..."

find /root /opt/kinetics-gateway \
    -maxdepth 3 \
    \( \
        -iname '*patch*' \
        -o -iname '*backup*' \
        -o -iname '*version*' \
    \) \
    -print \
    2>/dev/null |
sort \
    > "$BUNDLE/patch-history/PATCH_AND_BACKUP_PATHS.txt"

if [ -d /opt/kinetics-gateway/.git ]; then
    git -C /opt/kinetics-gateway status \
        > "$BUNDLE/patch-history/git-status.txt" \
        2>&1 || true

    git -C /opt/kinetics-gateway log \
        --oneline -30 \
        > "$BUNDLE/patch-history/git-log-last-30.txt" \
        2>&1 || true

    git -C /opt/kinetics-gateway rev-parse HEAD \
        > "$BUNDLE/patch-history/git-commit.txt" \
        2>&1 || true
fi

echo "Generating documentation..."

cat > "$BUNDLE/README_FIRST.txt" <<EOF
KINETICS GATEWAY - DEPLOYED RUNNING SNAPSHOT

Gateway hostname:
$(hostname)

Capture time:
$(date -Is)

Actual deployed source:
opt/kinetics-gateway/

Active gateway configuration:
etc/kinetics-gateway/

Cloudflare configuration:
etc/cloudflared/

Systemd service definitions:
systemd/

Runtime diagnostics:
diagnostics/

Operational scripts:
operational-scripts/

The Python virtual environment is excluded.
Installed dependency versions are recorded in:
diagnostics/pip-freeze.txt

This package may contain credentials and tunnel configuration.
Keep it private.
EOF

cat > "$BUNDLE/README_PC_MAINTENANCE.txt" <<'EOF'
PC DEVELOPMENT AND MAINTENANCE NOTES

Main gateway application:
opt/kinetics-gateway/

Configuration:
etc/kinetics-gateway/

Cloudflare:
etc/cloudflared/

Systemd:
systemd/

Diagnostics and OOM records:
diagnostics/

Safe-stop and monitoring scripts:
operational-scripts/

Do not deploy this entire directory directly onto a live gateway.
Create controlled patches with backups, syntax validation, tests and rollback.
EOF

cat > "$BUNDLE/LATEST_VERSION.txt" <<EOF
Package type: Actual deployed gateway snapshot
Gateway hostname: $(hostname)
Capture time: $(date -Is)

Gateway service command:
$(systemctl show kinetics-gateway.service -p ExecStart --value 2>/dev/null || true)

Git revision:
$(git -C /opt/kinetics-gateway rev-parse HEAD 2>/dev/null || echo "No Git revision available")
EOF

cat > "$BUNDLE/CHANGELOG_DEPLOYED_SNAPSHOT.txt" <<'EOF'
This bundle is an exact snapshot of the source currently deployed under
/opt/kinetics-gateway.

It does not assign an invented application version.

Previously applied patches may already be merged into the deployed source.
Review patch-history, source timestamps, Git data and diagnostics to reconstruct
the deployment history.
EOF

find "$BUNDLE/opt/kinetics-gateway" \
    -type f \
    -printf '%P\n' |
sort \
    > "$BUNDLE/SOURCE_MANIFEST.txt"

cat > "$BUNDLE/UPDATE_MANIFEST.txt" <<'EOF'
INCLUDED

1. Actual gateway application source
2. Active gateway configuration
3. Cloudflare configuration
4. Gateway-related systemd services
5. Safe-stop and monitoring scripts
6. Python dependency list
7. Runtime diagnostics
8. Gateway and Cloudflare journals
9. Kernel OOM evidence
10. Patch and backup path inventory

EXCLUDED

1. Python virtual environment
2. Python cache files
3. SQLite WAL and SHM files
EOF

{
    echo "KINETICS GATEWAY SNAPSHOT TEST RESULTS"
    echo
    echo "Capture time: $(date -Is)"
    echo
    echo "Service active:"
    systemctl is-active kinetics-gateway.service 2>&1 || true
    echo
    echo "Service enabled:"
    systemctl is-enabled kinetics-gateway.service 2>&1 || true
    echo
    echo "API health:"
    cat "$BUNDLE/diagnostics/api-health.txt"
} > "$BUNDLE/TEST_RESULTS.txt"

echo "Generating checksums..."

(
    cd "$BUNDLE"

    find . \
        -type f \
        ! -name SHA256SUMS.txt \
        -print0 |
    sort -z |
    xargs -0 sha256sum \
        > SHA256SUMS.txt
)

echo "Creating final archive..."

tar \
    -C /root \
    -czf "$ARCHIVE" \
    "$NAME"

echo
echo "Structured package created:"
ls -lh "$ARCHIVE"

echo
echo "SHA-256:"
sha256sum "$ARCHIVE"

echo
echo "Top-level package:"
find "$BUNDLE" \
    -maxdepth 2 \
    -mindepth 1 \
    -printf '%P\n' |
sort
