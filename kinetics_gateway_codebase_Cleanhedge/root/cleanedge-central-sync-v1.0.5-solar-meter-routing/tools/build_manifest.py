#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    data = json.loads(args.baseline.read_text(encoding="utf-8"))
    changed = []
    for point in data["points"]:
        if (
            point.get("vendor") == "cleanedge_solar"
            and point.get("scope") == "solar"
            and point.get("substream") == "combiner_box_meter"
        ):
            point["substream"] = "solar_inverter"
            changed.append(point["point_id"])

    assert len(changed) == 12, changed
    assert len(data["points"]) == 1786
    assert data["expected_runtime_signals"] == 6184
    assert not [p for p in data["points"] if p["substream"] == "combiner_box_meter"]
    assert len([p for p in data["points"] if p["substream"] == "solar_inverter"]) == 42

    data["cadence_revision"] = "cleanedge-cadence-v1.0.5"
    data["routing_revision"] = {
        "revision": "cleanedge-solar-meter-s2.11-v1.0.5",
        "changed_points": 12,
        "from_substream": "combiner_box_meter",
        "to_substream": "solar_inverter",
        "contract_effect": "routing only; point fields and cadence unchanged",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("Changed solar/grid-meter policies:", len(changed))
    print("SHA256:", hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
