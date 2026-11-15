#!/usr/bin/env python3
"""Build the approved CleanEdge v1.0.4 cadence-only S2 manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


EXPECTED_POINTS = 1786
EXPECTED_RUNTIME_SIGNALS = 6184
EXPECTED_CONTRACT_SHA256 = (
    "5eaf98713c152cfae48eca5705bc369d6c61d6034b4bebab30b101e59f00d96e"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    document = json.loads(args.baseline.read_text(encoding="utf-8"))
    assert len(document["points"]) == EXPECTED_POINTS
    assert document["expected_canonical_points"] == EXPECTED_POINTS
    assert document["expected_runtime_signals"] == EXPECTED_RUNTIME_SIGNALS
    assert document["contract_sha256"] == EXPECTED_CONTRACT_SHA256

    changed: Counter[tuple[str, float, float]] = Counter()
    runtime_changed: Counter[str] = Counter()

    for point in document["points"]:
        if point["substream"] != "bms_extended":
            continue
        if point.get("element_index") is None:
            continue

        key = point.get("key")
        old_interval = float(point["interval_sec"])
        if key == "vcell" and old_interval == 30.0:
            new_interval = 120.0
        elif key == "tcell" and old_interval == 30.0:
            new_interval = 180.0
        else:
            continue

        point["interval_sec"] = new_interval
        changed[(key, old_interval, new_interval)] += 1
        runtime_changed[key] += int(point.get("expected_instances") or 1)

    assert changed == Counter({
        ("vcell", 30.0, 120.0): 416,
        ("tcell", 30.0, 180.0): 416,
    })
    assert runtime_changed == Counter({"vcell": 1664, "tcell": 1664})

    document["cadence_revision"] = "cleanedge-cadence-v1.0.4"
    document["cadence_basis"] = {
        "reference": "Kalpa S2 category policy",
        "reference_sha256": (
            "40c5e29be9b4dd7d957c29f9d6aa6a57b4ee59c83fe300b6fb61a1d6fde9a69f"
        ),
        "cell_voltage_interval_sec": 120.0,
        "cell_temperature_interval_sec": 180.0,
        "scope": "cadence only; coverage and backend contract unchanged",
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"Built {args.output}")
    print(f"SHA256 {digest}")
    print("Changed 832 canonical definitions / 3328 runtime signals")


if __name__ == "__main__":
    main()
