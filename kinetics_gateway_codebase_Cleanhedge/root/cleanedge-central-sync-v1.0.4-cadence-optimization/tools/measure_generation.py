#!/usr/bin/env python3
"""Measure recent producer generation without changing gateway state."""

from __future__ import annotations

import argparse
import sqlite3
import time
from collections import defaultdict


DATABASE = "/var/lib/kinetics-central-sync/central_sync.db"


def snapshot() -> dict[tuple[str, str], tuple[int, int]]:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=10)
    try:
        rows = connection.execute(
            "SELECT stream, substream, messages, payload_bytes "
            "FROM generation_metrics_v1"
        ).fetchall()
    finally:
        connection.close()
    return {
        (stream, substream or "-"): (int(messages), int(payload_bytes))
        for stream, substream, messages, payload_bytes in rows
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    start_time = time.time()
    start = snapshot()
    print(f"Observing production for {args.seconds} seconds...")
    time.sleep(args.seconds)
    elapsed_min = (time.time() - start_time) / 60.0
    end = snapshot()
    totals = defaultdict(lambda: [0, 0])

    print(f"{'Stream':38} {'Substream':30} {'msg/min':>10} {'MB/min':>10}")
    rows = []
    for key in set(start) | set(end):
        dm = max(0, end.get(key, (0, 0))[0] - start.get(key, (0, 0))[0])
        db = max(0, end.get(key, (0, 0))[1] - start.get(key, (0, 0))[1])
        totals["all"][0] += dm
        totals["all"][1] += db
        rows.append((db, key[0], key[1], dm / elapsed_min, db / elapsed_min / 1e6))
    for _, stream, substream, msg_rate, byte_rate in sorted(rows, reverse=True):
        if msg_rate:
            print(f"{stream:38} {substream:30} {msg_rate:10.2f} {byte_rate:10.3f}")
    print("Total:", round(totals["all"][0] / elapsed_min, 2), "messages/min,")
    print(round(totals["all"][1] / elapsed_min / 1e6, 3), "MB/min")
    print("This is producer generation, not backend acceptance.")


if __name__ == "__main__":
    main()
