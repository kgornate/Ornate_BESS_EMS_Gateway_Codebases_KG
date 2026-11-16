import time
from datetime import datetime, timezone
from pathlib import Path

base = Path("/opt/ornate-ems-gateway/backend")
cfg = json.loads(
    Path("/etc/ornate-ems-gateway/central_sync.json").read_text()
)
settings = cfg.get("overflow_recovery", {})
directory = Path(settings.get(
    "archive_dir",
    "/mnt/ems-logs/ornate-ems-gateway/backlog_archives"
))
if not directory.is_absolute():
    directory = base / directory

files = sorted(directory.glob(
    settings.get("archive_glob", "central_sync_overflow_*.db")
))
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
output = Path(f"/root/kalpa-backlog-inventory-{stamp}.json")

report = {
    "started_utc": datetime.now(timezone.utc).isoformat(),
    "archives_found": len(files),
    "archives": [],
    "errors": [],
}
totals = {}
successful = 0

for index, path in enumerate(files, 1):
    connection = None
    try:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2
        )
        connection.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 8
        connection.set_progress_handler(
            lambda: int(time.monotonic() > deadline), 10000
        )

        columns = {
            row[1] for row in
            connection.execute("PRAGMA table_info(messages)")
        }
        required = {"state", "payload_bytes"}
        if not required.issubset(columns):
            raise RuntimeError(
                "Unexpected messages schema; missing "
                + str(sorted(required - columns))
            )

        states = {}
        for state, count, size in connection.execute("""
            SELECT state, COUNT(*), COALESCE(SUM(payload_bytes), 0)
            FROM messages
            GROUP BY state
        """):
            states[state] = {"messages": count, "payload_bytes": size}
            total = totals.setdefault(
                state, {"messages": 0, "payload_bytes": 0}
            )
            total["messages"] += count
            total["payload_bytes"] += size

        report["archives"].append({
            "file": path.name,
            "states": states,
        })
        successful += 1
    except Exception as exc:
        report["errors"].append({
            "file": path.name,
            "error": f"{type(exc).__name__}: {exc}",
        })
    finally:
        if connection is not None:
            connection.close()

    if index % 20 == 0:
        print(f"Scanned {index}/{len(files)} archives...", flush=True)
    time.sleep(0.05)

report["finished_utc"] = datetime.now(timezone.utc).isoformat()
report["archives_scanned_successfully"] = successful
report["totals_by_state"] = totals
report["limits"] = [
    "Counts are stored copies; duplicate IDs across archives may remain.",
    "Uploads and archival continue; this is not an atomic snapshot.",
    "Errors make totals incomplete.",
    "ACKed rows and retained archive files are not pending backlog.",
]

output.write_text(json.dumps(report, indent=2))
output.chmod(0o600)

print(f"\nArchives scanned successfully: {successful}/{len(files)}")
print("\nSTATE | MESSAGES | LOGICAL PAYLOAD MB")
for state, values in sorted(totals.items()):
    print(
        f"{state} | {values['messages']} | "
        f"{values['payload_bytes']/1_000_000:.3f}"
    )

pending = [
    values for state, values in totals.items()
    if state in {"pending", "retry", "inflight"}
]
print("\nOutstanding stored copies:",
      sum(v["messages"] for v in pending))
print("Outstanding logical payload MB:",
      round(sum(v["payload_bytes"] for v in pending)/1_000_000, 3))
print("Errors:", len(report["errors"]))
print("Share:", output)
PY

python3 - <<'PY'
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

base = Path("/opt/ornate-ems-gateway/backend")
cfg = json.loads(Path(
    "/etc/ornate-ems-gateway/central_sync.json"
).read_text())
settings = cfg.get("overflow_recovery", {})
directory = Path(settings.get(
    "archive_dir",
    "/mnt/ems-logs/ornate-ems-gateway/backlog_archives"
))
if not directory.is_absolute():
    directory = base / directory

files = sorted(directory.glob(
    settings.get("archive_glob", "central_sync_overflow_*.db")
))
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
output = Path(f"/root/kalpa-backlog-inventory-{stamp}.json")
report = {
    "started_utc": datetime.now(timezone.utc).isoformat(),
    "archives_found": len(files),
    "archives": [],
    "errors": [],
}
totals = {}

for index, path in enumerate(files, 1):
    conn = None
    try:
        conn = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True, timeout=2
        )
        conn.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 10
        conn.set_progress_handler(
            lambda: int(time.monotonic() > deadline), 10000
        )

        # Finish the read before adding any totals.
        rows = conn.execute("""
            SELECT state, COUNT(*), COALESCE(SUM(payload_bytes), 0)
            FROM outbox_messages
            GROUP BY state
        """).fetchall()

        states = {}
        for state, count, size in rows:
            states[state] = {
                "messages": count, "payload_bytes": size
            }
            total = totals.setdefault(
                state, {"messages": 0, "payload_bytes": 0}
            )
            total["messages"] += count
            total["payload_bytes"] += size

        report["archives"].append({
            "file": path.name, "states": states
        })
    except Exception as exc:
        error = {
            "file": path.name,
            "error": f"{type(exc).__name__}: {exc}",
        }
        report["errors"].append(error)
        if len(report["errors"]) <= 3:
            print("READ ERROR:", error, flush=True)
    finally:
        if conn is not None:
            conn.close()

    if index % 20 == 0:
        print(f"Scanned {index}/{len(files)}...", flush=True)
    time.sleep(0.05)

report["finished_utc"] = datetime.now(timezone.utc).isoformat()
report["archives_scanned_successfully"] = len(report["archives"])
report["totals_by_state"] = totals
report["limits"] = [
    "Stored copies, not deduplicated message IDs.",
    "Concurrent uploads and archival; not an atomic snapshot.",
    "Read errors make totals incomplete.",
]
output.write_text(json.dumps(report, indent=2))
output.chmod(0o600)

print(f"\nSuccessful: {len(report['archives'])}/{len(files)}")
print("STATE | MESSAGES | PAYLOAD MB")
for state, values in sorted(totals.items()):
    print(
        f"{state} | {values['messages']} | "
        f"{values['payload_bytes']/1e6:.3f}"
    )

waiting = [
    values for state, values in totals.items()
    if state in {"pending", "retry", "inflight"}
]
if report["archives"]:
    print("\nOutstanding stored copies:",
          sum(v["messages"] for v in waiting))
    print("Outstanding payload MB:",
          round(sum(v["payload_bytes"] for v in waiting)/1e6, 3))
else:
    print("\nInventory unavailable: no successful reads.")

print("Errors:", len(report["errors"]))
print("Share:", output)
PY

kalpa-delivery-dashboard 
exit
python3 - <<'PY'
import io
import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

now = datetime.now(timezone.utc)
stamp = now.strftime("%Y%m%dT%H%M%SZ")
output = Path(f"/root/kalpa-last-day-review-{stamp}.tar.gz")
base = Path("/opt/ornate-ems-gateway/backend")
cfg = json.loads(Path(
    "/etc/ornate-ems-gateway/central_sync.json"
).read_text())
errors = []
included = []

with tarfile.open(output, "w:gz") as bundle:
    def add_bytes(name, content):
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o600
        bundle.addfile(info, io.BytesIO(content))

    def add_file(path, name):
        try:
            # Capture a fixed length even if the logger keeps appending.
            with path.open("rb") as source:
                size = path.stat().st_size
                info = tarfile.TarInfo(name)
                info.size = size
                info.mode = 0o600
                bundle.addfile(info, source)
            included.append(str(path))
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")

    for name, command in (
        ("central-sync-last-24h.txt", [
            "journalctl", "-u", "ornate-central-sync.service",
            "--since", "24 hours ago", "--utc",
            "--no-pager", "-o", "short-iso"
        ]),
        ("central-sync-service.txt", [
            "systemctl", "show", "ornate-central-sync.service",
            "-p", "ActiveState", "-p", "SubState",
            "-p", "Result", "-p", "NRestarts",
            "-p", "ExecMainStartTimestamp"
        ]),
        ("available-journal-boots.txt", [
            "journalctl", "--list-boots", "--no-pager"
        ]),
    ):
        print("Collecting:", name, flush=True)
        result = subprocess.run(command, capture_output=True)
        add_bytes(name, result.stdout + result.stderr)

    for name, value in (
        ("current-live-status.json", cfg.get(
            "status_file", "/var/lib/ornate-central-sync/status.json"
        )),
        ("current-recovery-status.json",
         cfg.get("overflow_recovery", {}).get(
             "status_file",
             "/var/lib/ornate-central-sync/overflow_recovery_status.json"
         )),
        ("current-archiver-status.json",
         cfg.get("overflow_archive", {}).get(
             "status_file",
             "/var/lib/ornate-central-sync/overflow_archiver_status.json"
         )),
    ):
        path = Path(value)
        if not path.is_absolute():
            path = base / path
        add_file(path, name)

    cutoff = now.timestamp() - 86400
    logdir = Path("/root/kalpa-dashboard-logs")
    if logdir.exists():
        for path in sorted(logdir.glob("delivery-*.log")):
            if path.stat().st_mtime >= cutoff:
                print("Collecting dashboard:", path.name, flush=True)
                add_file(path, "dashboard/" + path.name)

    for path in sorted(Path("/root").glob(
        "kalpa-backlog-inventory-*.json"
    )):
        add_file(path, "inventories/" + path.name)

    for pattern in (
        "kalpa-live-archive-24h-*",
        "kalpa-v22-archive-24h-*",
    ):
        for folder in sorted(Path("/root").glob(pattern)):
            if folder.is_dir():
                for name in ("completion.json", "summary.json",
                             "summary.partial.json"):
                    path = folder / name
                    if path.is_file():
                        add_file(path, f"captures/{folder.name}/{name}")

    add_bytes("collection.json", json.dumps({
        "collected_utc": now.isoformat(),
        "journal_requested": "last 24 hours",
        "included_files": included,
        "errors": errors,
    }, indent=2).encode())

output.chmod(0o600)
print("\nCollection errors:", len(errors))
print("Share:", output)
print("Size MB:", round(output.stat().st_size / 1e6, 2))
PY

python3 - <<'PY'
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

base = Path("/opt/ornate-ems-gateway/backend")
cfg = json.loads(Path(
    "/etc/ornate-ems-gateway/central_sync.json"
).read_text())
settings = cfg.get("overflow_recovery", {})
directory = Path(settings.get(
    "archive_dir",
    "/mnt/ems-logs/ornate-ems-gateway/backlog_archives"
))
if not directory.is_absolute():
    directory = base / directory

previous = None
for path in sorted(
    Path("/root").glob("kalpa-backlog-inventory-*.json"),
    reverse=True
):
    try:
        data = json.loads(path.read_text())
        if (
            data.get("archives_found", 0) > 0
            and data.get("archives_scanned_successfully")
                == data["archives_found"]
            and not data.get("errors")
        ):
            previous = (path, data)
            break
    except Exception:
        pass

files = sorted(directory.glob(
    settings.get("archive_glob", "central_sync_overflow_*.db")
))
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
output = Path(f"/root/kalpa-backlog-inventory-{stamp}.json")
report = {
    "started_utc": datetime.now(timezone.utc).isoformat(),
    "archives_found": len(files),
    "archives": [],
    "errors": [],
}
totals = {}

for index, path in enumerate(files, 1):
    conn = None
    try:
        conn = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True, timeout=2
        )
        conn.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 10
        conn.set_progress_handler(
            lambda: int(time.monotonic() > deadline), 10000
        )
        rows = conn.execute("""
            SELECT state, COUNT(*), COALESCE(SUM(payload_bytes), 0)
            FROM outbox_messages
            GROUP BY state
        """).fetchall()

        states = {}
        for state, count, size in rows:
            states[state] = {
                "messages": count, "payload_bytes": size
            }
            total = totals.setdefault(
                state, {"messages": 0, "payload_bytes": 0}
            )
            total["messages"] += count
            total["payload_bytes"] += size

        report["archives"].append({
            "file": path.name, "states": states
        })
    except Exception as exc:
        error = {
            "file": path.name,
            "error": f"{type(exc).__name__}: {exc}",
        }
        report["errors"].append(error)
        if len(report["errors"]) <= 3:
            print("READ ERROR:", error, flush=True)
    finally:
        if conn is not None:
            conn.close()

    if index % 20 == 0:
        print(f"Scanned {index}/{len(files)}...", flush=True)
    time.sleep(0.05)

report["finished_utc"] = datetime.now(timezone.utc).isoformat()
report["archives_scanned_successfully"] = len(report["archives"])
report["totals_by_state"] = totals
report["limits"] = [
    "Stored copies, not deduplicated message IDs.",
    "Concurrent uploads and archival; not an atomic snapshot.",
    "Read errors make totals incomplete.",
]
output.write_text(json.dumps(report, indent=2))
output.chmod(0o600)

def outstanding(states):
    selected = [
        value for state, value in states.items()
        if state in {"pending", "retry", "inflight"}
    ]
    return (
        sum(v["messages"] for v in selected),
        sum(v["payload_bytes"] for v in selected),
    )

print(f"\nSuccessful: {len(report['archives'])}/{len(files)}")
print("STATE | MESSAGES | PAYLOAD MB")
for state, values in sorted(totals.items()):
    print(
        f"{state} | {values['messages']} | "
        f"{values['payload_bytes']/1e6:.3f}"
    )

if report["archives"]:
    messages, size = outstanding(totals)
    print("\nOutstanding stored copies:", messages)
    print("Outstanding payload GB:", round(size/1e9, 3))

    if previous and not report["errors"]:
        old_path, old = previous
        old_messages, old_size = outstanding(old["totals_by_state"])
        print("\nPrevious inventory:", old_path.name)
        print("Previous finished UTC:", old.get("finished_utc"))
        print("Message-copy reduction:", old_messages - messages)
        print("Payload reduction GB:", round((old_size-size)/1e9, 3))
else:
    print("\nInventory unavailable: no successful reads.")

print("Errors:", len(report["errors"]))
print("Share:", output)
PY

kalpa-delivery-dashboard 
