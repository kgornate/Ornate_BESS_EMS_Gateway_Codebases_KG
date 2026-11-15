#!/usr/bin/env python3
"""Validate the installed v1.0.4 manifest and its immutable config pin."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


CONFIG = Path("/etc/kinetics-gateway/central_sync.json")
MANIFEST = Path(
    "/opt/kinetics-gateway/backend/configs/cleanedge_s2_policy_frozen_v1_0.json"
)


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    general = config["general_assets"]

    assert general["s2_policy_expected_sha256"] == digest
    assert manifest["cadence_revision"] == "cleanedge-cadence-v1.0.4"
    assert manifest["expected_canonical_points"] == 1786
    assert manifest["expected_runtime_signals"] == 6184
    assert len(manifest["points"]) == 1786
    assert config["command_downlink"]["enabled"] is False

    policies = Counter()
    for point in manifest["points"]:
        if (
            point["substream"] == "bms_extended"
            and point.get("element_index") is not None
            and point.get("key") in {"vcell", "tcell"}
        ):
            policies[(point["key"], float(point["interval_sec"]))] += 1

    assert policies == Counter({("vcell", 120.0): 416, ("tcell", 180.0): 416})

    # Validate through the deployed application parser as well.
    from app.central_sync.s2_policy import S2PolicyManifest

    S2PolicyManifest(str(MANIFEST), expected_sha256=digest)
    print("CleanEdge v1.0.4 cadence validation: PASS")
    print("Canonical points: 1786 (unchanged)")
    print("Runtime signals: 6184 (unchanged)")
    print("BMS cell voltage: PERIODIC 120 s")
    print("BMS cell temperature: PERIODIC 180 s")


if __name__ == "__main__":
    main()
