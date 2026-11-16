#!/usr/bin/env python3
"""Offline, targeted update. No OS, network, dependency or Central Sync changes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

PACKAGE = Path(__file__).resolve().parent
SERVICE = "ornate-ems-gateway.service"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode=0o644, uid=0, gid=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".acrel-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.chown(name, uid, gid)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare(backend):
    """Apply exact contextual changes in memory; preserve unrelated updates."""
    patches = json.loads((PACKAGE / "integration-patches.json").read_text())
    planned = {}
    for name, chunks in patches.items():
        current = (backend / name).read_text()
        for chunk in chunks:
            if current.count(chunk["old"]) != 1:
                raise RuntimeError(f"Cannot cleanly patch {name}; no files changed. Supply its current version for reconciliation.")
            current = current.replace(chunk["old"], chunk["new"], 1)
        planned[name] = current.encode()
    for source in (PACKAGE / "payload").rglob("*.py"):
        name = str(source.relative_to(PACKAGE / "payload"))
        if (backend / name).exists():
            raise RuntimeError(f"{name} already exists; refusing duplicate installation")
        planned[name] = source.read_bytes()
    return planned


def verify_package():
    hashes = json.loads((PACKAGE / "SHA256.json").read_text())
    for name, expected in hashes.items():
        path = PACKAGE / name
        if not path.resolve().is_relative_to(PACKAGE):
            raise RuntimeError("Invalid package path")
        if digest(path.read_bytes()) != expected:
            raise RuntimeError(f"Package checksum mismatch: {name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-meter", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--backend", type=Path, default=Path("/opt/ornate-ems-gateway/backend"))
    parser.add_argument("--config", type=Path, default=Path("/etc/ornate-ems-gateway/config.json"))
    args = parser.parse_args()
    if os.geteuid() != 0 and not args.check_only:
        parser.error("Run as root on DEBIX")
    verify_package()
    backend = args.backend.resolve()
    config_path = args.config.resolve()
    if not backend.is_dir() or not config_path.is_file():
        parser.error("Existing application/configuration not found")
    planned = prepare(backend)
    previous_config = config_path.read_bytes()
    payload = json.loads(previous_config)
    if "meter" in payload:
        raise RuntimeError("Meter configuration already exists; preserve/reconcile it before this first-install update")
    payload["meter"] = {
        "enabled": args.enable_meter, "asset_id": "acrel_meter_1", "label": "ACREL APM520",
        "location": "unassigned", "mode": "hardware", "device": "/dev/ttyLP6",
        "unit_id": 1, "baudrate": 9600, "parity": "N", "stopbits": 1,
        "word_order": "big", "timeout_seconds": 2, "retries": 1,
        "poll_seconds": 5, "stale_after_seconds": 20,
        "energy_enabled": True, "energy_poll_seconds": 60, "scaling_validated": False,
    }
    candidate = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()
    if args.enable_meter:
        if not Path("/dev/ttyLP6").exists() and not args.check_only:
            raise RuntimeError("Verified RS485 device /dev/ttyLP6 is missing")
        for name in ("RS485_nRE1", "RS485_nDE1"):
            control = Path("/sys/class/leds") / name / "brightness"
            if not args.check_only and (not control.is_file() or control.read_text().strip() != "1"):
                raise RuntimeError(f"{name} must match the verified default value 1. No GPIO settings changed.")

    # Compile and validate using the gateway's own Python/dependencies before stopping it.
    with tempfile.TemporaryDirectory(prefix="kalpa-acrel-stage-") as directory:
        stage = Path(directory)
        shutil.copytree(backend / "app", stage / "app", ignore=shutil.ignore_patterns("__pycache__"))
        for name, data in planned.items():
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            py_compile.compile(str(destination), doraise=True)
        staged_config = stage / "candidate.json"
        staged_config.write_bytes(candidate)
        staged_config.chmod(0o600)
        check = '''import json,sys
from app.core.config import GatewayConfig
try:
    GatewayConfig.model_validate(json.load(open(sys.argv[1])))
    import serial
    import app.api.routes
except Exception as exc:
    print("Preflight configuration/import validation failed:", type(exc).__name__)
    sys.exit(1)
print("Preflight configuration and imports: PASS")
'''
        env = dict(os.environ, PYTHONPATH=str(stage), PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([sys.executable, "-c", check, str(staged_config)], cwd=stage, env=env, check=True)
    if args.check_only:
        print("CHECK ONLY: clean patch and configuration validated; no deployed files changed.")
        return

    backup = Path("/root") / ("kalpa-acrel-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(mode=0o700)
    targets = {backend / name: data for name, data in planned.items()}
    targets[config_path] = candidate
    originals = {}
    for index, (path, replacement) in enumerate(targets.items()):
        if path.is_symlink():
            raise RuntimeError(f"Refusing to replace symbolic link: {path}")
        existed = path.exists()
        stat = path.stat() if existed else None
        original = path.read_bytes() if existed else None
        saved = backup / f"original-{index}"
        if original is not None:
            saved.write_bytes(original)
            saved.chmod(0o600)
        originals[str(path)] = dict(
            existed=existed, saved=str(saved),
            mode=(stat.st_mode & 0o777) if stat else 0o644,
            uid=stat.st_uid if stat else 0, gid=stat.st_gid if stat else 0,
            before_sha256=digest(original) if original is not None else None,
            installed_sha256=digest(replacement),
        )
    if config_path.read_bytes() != previous_config:
        raise RuntimeError("Configuration changed during preflight; retry with a stable configuration")
    (backup / "restore.json").write_text(json.dumps(originals, indent=2))
    (backup / "restore.json").chmod(0o600)
    shutil.copy2(PACKAGE / "rollback.py", backup / "rollback.py")
    was_active = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0
    if not was_active:
        raise RuntimeError("Gateway service is not active; start/diagnose it before applying this update")
    subprocess.run(["systemctl", "stop", SERVICE], check=True, timeout=30)
    changed = []
    try:
        for path, replacement in targets.items():
            record = originals[str(path)]
            before = path.read_bytes() if path.exists() else None
            if (digest(before) if before is not None else None) != record["before_sha256"]:
                raise RuntimeError(f"File changed during preflight: {path}")
            changed.append(path)
            atomic_write(path, replacement, record["mode"], record["uid"], record["gid"])
        subprocess.run(["systemctl", "start", SERVICE], check=True, timeout=30)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for attempt in range(20):
            try:
                with opener.open("http://127.0.0.1:8000/api/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("Gateway API did not recover after update")
    except BaseException:
        subprocess.run(["systemctl", "stop", SERVICE], timeout=30)
        for path in reversed(changed):
            record = originals[str(path)]
            if record["existed"]:
                atomic_write(path, Path(record["saved"]).read_bytes(), record["mode"], record["uid"], record["gid"])
            else:
                path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "start", SERVICE], timeout=30)
        print("Update failed; original application/configuration restored. Backup:", backup)
        raise
    print("ACREL integration installed. Meter enabled:", args.enable_meter)
    print("Backup and manual rollback:", backup)
    print("Run verify_meter.py next. API startup does not itself prove meter communication.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("INSTALL FAILED:", error, file=sys.stderr)
        sys.exit(1)
