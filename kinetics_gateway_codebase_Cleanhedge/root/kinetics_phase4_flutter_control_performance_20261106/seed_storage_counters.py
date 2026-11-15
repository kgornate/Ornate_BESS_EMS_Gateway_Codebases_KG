from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def database_path() -> Path:
    config_path = Path(os.environ.get("KINETICS_CONFIG", "/etc/kinetics-gateway/config.json"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    storage = config["storage"]
    preferred = Path(storage.get("preferred_root", "/mnt/ems-logs/kinetics-gateway"))
    fallback = Path(storage.get("fallback_root", "/opt/kinetics-gateway/backend/data"))
    mount_point = Path(storage.get("preferred_mount_point", "/mnt/ems-logs"))
    root = preferred if mount_point.is_mount() else fallback
    return root / storage.get("database_name", "kinetics_gateway.db")


path = database_path()
connection = sqlite3.connect(path, timeout=60)
connection.row_factory = sqlite3.Row
with connection:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS storage_counters (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
    )
    counts = {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("telemetry", "alarms", "alarm_history", "command_audit", "events")
    }
    totals = connection.execute(
        """
        SELECT COALESCE(SUM(uncompressed_bytes),0), COALESCE(SUM(stored_bytes),0)
        FROM telemetry
        """
    ).fetchone()
    values = {
        **{f"records.{table}": count for table, count in counts.items()},
        "telemetry.uncompressed_bytes_total": int(totals[0]),
        "telemetry.stored_bytes_total": int(totals[1]),
    }
    connection.executemany(
        """
        INSERT INTO storage_counters(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        values.items(),
    )
connection.close()

if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(descriptor)

print(f"Seeded exact storage counters for {path}")
print(counts)
