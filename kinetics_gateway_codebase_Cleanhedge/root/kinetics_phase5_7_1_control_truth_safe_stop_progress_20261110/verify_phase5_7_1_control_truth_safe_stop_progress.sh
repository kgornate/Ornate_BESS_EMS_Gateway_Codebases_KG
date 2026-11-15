#!/bin/bash
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
cd "$PKG"
sha256sum -c PACKAGE_SHA256SUMS
cd /
sha256sum -c "$PKG/BASELINE_SHA256SUMS"
echo "Phase 5.7.1 cumulative payload and installed Phase 5.6 baseline verified."

