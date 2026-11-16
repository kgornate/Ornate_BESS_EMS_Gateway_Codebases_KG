#!/usr/bin/env python3
"""Static HMI-only update. No backend, configuration or service restart."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
NAMES = ("app.js", "styles.css", "index.html")  # Publish HTML last.


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, data, mode, uid, gid):
    fd, temporary = tempfile.mkstemp(prefix=".kalpa-hmi-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def plan(hmi):
    baseline = json.loads((ROOT / "BASELINE.json").read_text())
    for name, expected in json.loads((ROOT / "SHA256.json").read_text()).items():
        file = ROOT / name
        if not file.resolve().is_relative_to(ROOT) or sha(file.read_bytes()) != expected:
            raise RuntimeError(f"Package checksum failed: {name}")
    if not (hmi.parent / "assets/acrel_meter.py").is_file():
        raise RuntimeError("Install the ACREL backend v0.1 before the HMI update")
    result = {}
    for name in NAMES:
        path = hmi / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Expected regular HMI file: {path}")
        before, after = path.read_bytes(), (ROOT / "hmi" / name).read_bytes()
        if before != after and sha(before) != baseline[name]:
            raise RuntimeError(f"Current {name} has separate changes; no files changed. Share it for reconciliation.")
        stat = path.stat()
        result[name] = dict(before=before, after=after, mode=stat.st_mode & 0o777, uid=stat.st_uid, gid=stat.st_gid)
    return result


def restore(directory):
    records = json.loads((directory / "restore.json").read_text())
    for name, record in records.items():
        path = Path(record["target"])
        if path.is_symlink() or sha(path.read_bytes()) != record["installed_sha256"]:
            raise RuntimeError(f"Refusing rollback over later changes: {path}")
        if sha((directory / name).read_bytes()) != record["original_sha256"]:
            raise RuntimeError(f"Backup checksum failed: {name}")
    for name in NAMES:
        record = records[name]
        write_atomic(Path(record["target"]), (directory / name).read_bytes(), record["mode"], record["uid"], record["gid"])
    print("Previous HMI restored. Refresh browser with Ctrl+F5.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hmi-dir", type=Path, default=Path("/opt/ornate-ems-gateway/backend/app/local_hmi"))
    parser.add_argument("--backup-root", type=Path, default=Path("/root"))
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 and not args.check_only:
        raise RuntimeError("Run as root on DEBIX")
    if args.rollback:
        restore(args.rollback.resolve())
        return
    hmi = args.hmi_dir.resolve()
    records = plan(hmi)
    if all(item["before"] == item["after"] for item in records.values()):
        print("This meter HMI is already installed. Refresh browser with Ctrl+F5.")
        return
    if args.check_only:
        print("Preflight PASS: meter backend found; three HMI files match the supported baseline. No changes made.")
        return
    backup = args.backup_root / ("kalpa-meter-hmi-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(mode=0o700)
    metadata = {}
    for name, record in records.items():
        original = backup / name
        original.write_bytes(record["before"])
        original.chmod(0o600)
        metadata[name] = dict(target=str(hmi / name), mode=record["mode"], uid=record["uid"], gid=record["gid"],
            installed_sha256=sha(record["after"]), original_sha256=sha(record["before"]))
    (backup / "restore.json").write_text(json.dumps(metadata, indent=2))
    (backup / "restore.json").chmod(0o600)
    updated = []
    try:
        for name in NAMES:
            record = records[name]
            if (hmi / name).read_bytes() != record["before"]:
                raise RuntimeError(f"{name} changed during preflight")
            updated.append(name)
            write_atomic(hmi / name, record["after"], record["mode"], record["uid"], record["gid"])
    except BaseException:
        for name in reversed(updated):
            record = records[name]
            write_atomic(hmi / name, record["before"], record["mode"], record["uid"], record["gid"])
        raise
    print("Energy meter HMI installed. Backend polling continues; no service restart needed.")
    print("Backup:", backup)
    print("Open /hmi/ and press Ctrl+F5. Select Energy meter in the sidebar.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("HMI UPDATE FAILED:", error, file=sys.stderr)
        sys.exit(1)
