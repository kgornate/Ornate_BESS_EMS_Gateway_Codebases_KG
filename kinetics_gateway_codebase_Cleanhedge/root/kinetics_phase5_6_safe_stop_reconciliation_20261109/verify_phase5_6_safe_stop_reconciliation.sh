#!/bin/bash
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
cd "$PKG"
sha256sum -c PACKAGE_SHA256SUMS
cd /
sha256sum -c "$PKG/BASELINE_SHA256SUMS"
echo "Phase 5.6 package and installed Phase 5.5.1 baseline verified."
