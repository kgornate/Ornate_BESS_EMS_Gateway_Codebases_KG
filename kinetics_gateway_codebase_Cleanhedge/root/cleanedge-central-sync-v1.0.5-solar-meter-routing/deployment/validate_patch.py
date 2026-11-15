#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


CONFIG = Path("/etc/kinetics-gateway/central_sync.json")
MANIFEST = Path("/opt/kinetics-gateway/backend/configs/cleanedge_s2_policy_frozen_v1_0.json")


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert config["general_assets"]["s2_policy_expected_sha256"] == digest
    assert config["command_downlink"]["enabled"] is False
    assert len(data["points"]) == 1786
    assert data["expected_runtime_signals"] == 6184
    counts = Counter(p["substream"] for p in data["points"])
    assert counts["combiner_box_meter"] == 0
    assert counts["solar_inverter"] == 42
    moved = [
        p for p in data["points"]
        if p.get("vendor") == "cleanedge_solar"
        and p.get("scope") == "solar"
        and p.get("point_id", "").startswith(("mfm.", "meter."))
    ]
    assert len(moved) == 12
    assert all(p["substream"] == "solar_inverter" for p in moved)

    from app.central_sync.s2_policy import S2PolicyManifest
    S2PolicyManifest(str(MANIFEST), expected_sha256=digest)
    print("CleanEdge v1.0.5 solar/grid-meter routing validation: PASS")
    print("Canonical points: 1786 (unchanged)")
    print("Runtime signals: 6184 (unchanged)")
    print("Moved policies: 12")
    print("Destination: S2.11 solar_inverter")
    print("combiner_box_meter active policies: 0")


if __name__ == "__main__":
    main()
