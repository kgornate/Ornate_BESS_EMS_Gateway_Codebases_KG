#!/usr/bin/env python3
"""Persistent solar-qualified scheduler for the Phase 5.7.2.2 control API."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_USER = os.getenv("API_USER", "internal")
API_PASS = os.getenv("API_PASS", "Internal@123")
CONFIG_PATH = Path(os.getenv("KINETICS_AUTOMATION_CONFIG", "/mnt/ems-logs/kinetics-gateway/automation_schedule.json"))
STATUS_PATH = Path(os.getenv("KINETICS_AUTOMATION_STATUS", "/mnt/ems-logs/kinetics-gateway/automation_status.json"))
LOCK_PATH = Path(os.getenv("LOCK_FILE", "/run/bess-seven-day-v302.lock"))
PAIR_IDS = ("pair_1", "pair_2", "pair_3", "pair_4")
POLL_SECONDS = float(os.getenv("AUTOMATION_POLL_SECONDS", "2"))
STOP = False


def log(message: str) -> None:
    print(f"{datetime.now(IST).isoformat()} {message}", flush=True)


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


class Gateway:
    def __init__(self) -> None:
        self.token: str | None = None

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 15) -> dict[str, Any]:
        encoded = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(BASE_URL + path, data=encoded, method=method)
        request.add_header("Accept", "application/json")
        if encoded is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(2 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            if error.code == 401 and path != "/api/auth/login":
                self.login()
                return self.request(method, path, body, timeout)
            raise RuntimeError(f"HTTP {error.code} {path}: {error.read(4096).decode(errors='replace')}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"gateway unavailable for {path}: {error}") from error
        return json.loads(raw) if raw else {}

    def login(self) -> None:
        self.token = None
        self.token = str(self.request("POST", "/api/auth/login", {"username": API_USER, "password": API_PASS})["access_token"])

    def wait_ready(self) -> None:
        while not STOP:
            try:
                health = self.request("GET", "/api/health", timeout=10)
                if health.get("status") == "ok" and (health.get("startup") or {}).get("ready"):
                    self.login()
                    return
            except Exception as error:
                log(f"Waiting for gateway: {error}")
            time.sleep(5)


def desired_window(config: dict[str, Any], now: datetime) -> str | None:
    if not config.get("enabled") or now.isoweekday() not in set(config.get("days") or []):
        return None
    current = now.strftime("%H:%M")
    for direction in ("charge", "discharge"):
        window = config.get(direction) or {}
        if str(window.get("start")) <= current < str(window.get("stop")):
            return direction
    return None


def solar_sample(view: dict[str, Any], max_age: int) -> tuple[float | None, bool, float | None]:
    solar = view.get("solar") or {}
    point = solar.get("active_power_kw") or {}
    value = None
    if str(point.get("q", point.get("quality", "unknown"))).lower() in {"good", "ok"}:
        try:
            value = float(point.get("v", point.get("value")))
        except (TypeError, ValueError):
            pass
    age = None
    try:
        stamp = datetime.fromisoformat(str(solar.get("timestamp") or view.get("timestamp")).replace("Z", "+00:00"))
        age = max(0.0, (datetime.now(stamp.tzinfo or IST) - stamp).total_seconds())
    except (TypeError, ValueError):
        pass
    fresh = bool(solar.get("online")) and value is not None and age is not None and age <= max_age
    return value, fresh, age


def target_tracking(item: dict[str, Any], direction: str, target: float) -> bool:
    summary = item.get("summary") or {}
    try:
        set_kw = float(summary.get("pcs_power_setpoint_kw") or 0)
        actual = float(summary.get("pcs_actual_power_kw") or 0)
    except (TypeError, ValueError):
        return False
    expected = -target if direction == "charge" else target
    return abs(set_kw - expected) <= 5 and target * .70 <= abs(actual) <= target * 1.30 and (actual < 0 if direction == "charge" else actual > 0)


def fully_stopped(item: dict[str, Any]) -> bool:
    s = item.get("summary") or {}
    try:
        return int(float(s.get("precharge_state") or 0)) == 0 and not s.get("positive_contactor_closed") and not s.get("negative_contactor_closed") and int(float(s.get("pcs_operating_state") or 0)) == 1 and abs(float(s.get("pcs_power_setpoint_kw") or 0)) <= .5 and abs(float(s.get("pcs_actual_power_kw") or 0)) <= 1
    except (TypeError, ValueError):
        return False


class Supervisor:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.phase = "starting"
        self.direction: str | None = None
        self.active_pairs: set[str] = set()
        self.above_since: float | None = None
        self.below_since: float | None = None
        self.solar_kw: float | None = None
        self.solar_fresh = False
        self.error: str | None = None
        self.config_revision: str | None = None

    def publish(self, message: str, **extra: Any) -> None:
        atomic_write(STATUS_PATH, {"version": 1, "build": "phase5.8-solar-time-automation", "running": True, "phase": self.phase, "direction": self.direction, "active_pairs": sorted(self.active_pairs), "solar_kw": self.solar_kw, "solar_fresh": self.solar_fresh, "message": message, "error": self.error, "timestamp": datetime.now(IST).isoformat(), **extra})

    def all_status(self) -> dict[str, Any]:
        return self.gateway.request("GET", "/api/control-sequence/status/all/compact")

    def stop_all(self, reason: str) -> None:
        self.phase = "safe_stopping"
        self.publish(reason)
        self.gateway.request("POST", "/api/control-sequence/safe-stop-all", {"confirmation": "EXECUTE_STAGE_WRITE", "open_bms": True}, timeout=30)
        deadline = time.monotonic() + 720
        while time.monotonic() < deadline:
            payload = self.all_status()
            progress = payload.get("safe_stop_all") or {}
            status = str(progress.get("status") or "running").lower()
            self.publish(f"Safe Stop All: {status}", safe_stop_all=progress)
            if status == "failed":
                raise RuntimeError(f"Safe Stop All failed: {progress}")
            pairs = payload.get("by_pair_id") or {}
            if status == "success" and all(fully_stopped(pairs.get(pair_id) or {}) for pair_id in PAIR_IDS):
                self.active_pairs.clear()
                self.direction = None
                self.phase = "waiting"
                self.publish("All four pairs verified safely stopped")
                return
            time.sleep(2)
        raise TimeoutError("Safe Stop All did not verify within 12 minutes")

    def start_pairs(self, config: dict[str, Any], direction: str) -> None:
        self.phase, self.direction = f"starting_{direction}", direction
        self.publish(f"Starting {direction} sequence")
        try:
            baseline = self.all_status().get("by_pair_id") or {}
            if any(not fully_stopped(baseline.get(pair_id) or {}) for pair_id in PAIR_IDS):
                self.stop_all("Automation start requires a verified safe baseline")
                self.direction = direction
                self.phase = f"starting_{direction}"
            startup_low_since: float | None = None
            for pair_id in PAIR_IDS:
                pair = (config.get("pairs") or {}).get(pair_id) or {}
                if not pair.get("enabled"):
                    continue
                target = float(pair[f"{direction}_kw"])
                self.gateway.request("POST", f"/api/control-sequence/{pair_id}/automatic-start", {"direction": direction, "power_kw": target, "ramp_step_kw": target, "ramp_interval_seconds": 1, "confirmation": "EXECUTE_AUTOMATIC_SEQUENCE"}, timeout=30)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    if desired_window(config, datetime.now(IST)) != direction:
                        raise RuntimeError("operating window ended during startup")
                    solar_cfg = config.get("solar") or {}
                    solar_view = self.gateway.request("GET", "/api/control-sequence/control-view", timeout=10)
                    solar_kw, solar_fresh, _ = solar_sample(
                        solar_view, int(solar_cfg.get("max_age_seconds", 15))
                    )
                    startup_low = (
                        not solar_fresh
                        or solar_kw is None
                        or solar_kw < float(solar_cfg.get("stop_below_kw", 80))
                    )
                    startup_low_since = startup_low_since or time.monotonic() if startup_low else None
                    if startup_low_since is not None and time.monotonic() - startup_low_since >= int(solar_cfg.get("stop_hold_seconds", 30)):
                        raise RuntimeError("solar loss persisted during startup")
                    item = self.gateway.request("GET", f"/api/control-sequence/{pair_id}/status/compact")
                    runtime = item.get("runtime") or {}
                    if str(runtime.get("run_status")).lower() == "failed":
                        raise RuntimeError(f"{pair_id} automatic start failed: {runtime}")
                    if target_tracking(item, direction, target):
                        self.active_pairs.add(pair_id)
                        self.publish(f"{pair_id} verified at {target:.1f} kW")
                        break
                    time.sleep(2)
                else:
                    raise TimeoutError(f"{pair_id} target tracking timed out")
        except Exception:
            self.stop_all(f"{direction} startup failure")
            raise
        if not self.active_pairs:
            raise RuntimeError("no enabled pair started")
        self.phase = f"active_{direction}"
        self.publish(f"{direction.capitalize()} active on {len(self.active_pairs)} pair(s)")

    def reconcile(self, config: dict[str, Any], direction: str) -> None:
        pairs = (self.all_status().get("by_pair_id") or {})
        for pair_id in self.active_pairs:
            setting = (config.get("pairs") or {}).get(pair_id) or {}
            if not setting.get("enabled") or not target_tracking(pairs.get(pair_id) or {}, direction, float(setting[f"{direction}_kw"])):
                self.stop_all(f"Active readback lost for {pair_id}")
                return

    def cycle(self, config: dict[str, Any], view: dict[str, Any], next_reconcile: float) -> float:
        solar = config.get("solar") or {}
        self.solar_kw, self.solar_fresh, age = solar_sample(view, int(solar.get("max_age_seconds", 15)))
        wanted, now = desired_window(config, datetime.now(IST)), time.monotonic()
        if wanted is None:
            self.above_since = self.below_since = None
            if self.active_pairs:
                self.stop_all("Schedule disabled or window ended")
            self.phase = "disabled" if not config.get("enabled") else "waiting_window"
            self.direction = None
            self.publish("Automation disabled" if not config.get("enabled") else "Waiting for next window")
        elif self.direction and wanted != self.direction:
            self.stop_all("Operating direction changed")
        elif self.active_pairs:
            low = not self.solar_fresh or self.solar_kw is None or self.solar_kw < float(solar.get("stop_below_kw", 80))
            if low:
                self.below_since = self.below_since or now
                elapsed = now - self.below_since
                self.phase = "qualifying_solar_loss"
                self.publish("Solar unavailable or below stop threshold", solar_age_seconds=age, stop_in_seconds=max(0, int(solar.get("stop_hold_seconds", 30) - elapsed)))
                if elapsed >= int(solar.get("stop_hold_seconds", 30)):
                    self.stop_all("Solar loss delay expired")
            else:
                self.below_since = None
                self.phase = f"active_{wanted}"
                if now >= next_reconcile:
                    self.reconcile(config, wanted)
                    next_reconcile = now + 30
                self.publish(f"{wanted.capitalize()} active", solar_age_seconds=age)
        else:
            self.direction, self.below_since = None, None
            high = self.solar_fresh and self.solar_kw is not None and self.solar_kw > float(solar.get("start_above_kw", 100))
            if high:
                self.above_since = self.above_since or now
                elapsed = now - self.above_since
                self.phase = "qualifying_solar"
                self.publish("Solar qualification in progress", solar_age_seconds=age, start_in_seconds=max(0, int(solar.get("start_hold_seconds", 60) - elapsed)))
                if elapsed >= int(solar.get("start_hold_seconds", 60)):
                    self.above_since = None
                    self.start_pairs(config, wanted)
            else:
                self.above_since = None
                self.phase = "waiting_solar"
                self.publish("Waiting for fresh solar above start threshold", solar_age_seconds=age)
        return next_reconcile

    def run(self) -> None:
        self.gateway.wait_ready()
        initial_config = read_json(CONFIG_PATH)
        if initial_config.get("enabled"):
            initial_pairs = self.all_status().get("by_pair_id") or {}
            if any(not fully_stopped(initial_pairs.get(pair_id) or {}) for pair_id in PAIR_IDS):
                self.stop_all("Enabled automation startup reconciliation")
        self.publish("Automation supervisor started")
        next_reconcile = 0.0
        while not STOP:
            try:
                config = read_json(CONFIG_PATH)
                revision = json.dumps(config, sort_keys=True)
                if self.config_revision and revision != self.config_revision and self.active_pairs:
                    self.stop_all("Configuration changed while active")
                self.config_revision = revision
                view = self.gateway.request("GET", "/api/control-sequence/control-view", timeout=10)
                next_reconcile = self.cycle(config, view, next_reconcile)
                self.error = None
            except Exception as error:
                self.error, self.phase = str(error), "fault"
                log(f"Supervisor cycle failed: {error}")
                self.publish("Automation fault; retrying")
                if self.active_pairs:
                    try:
                        self.stop_all("Automation supervisor fault")
                    except Exception as stop_error:
                        log(f"CRITICAL Safe Stop All retry required: {stop_error}")
            time.sleep(POLL_SECONDS)


def main() -> int:
    global STOP
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another scheduler owns the lock")
        return 2
    lock.write(str(os.getpid()))
    lock.flush()
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("STOP", True))
    signal.signal(signal.SIGINT, lambda *_: globals().__setitem__("STOP", True))
    supervisor = Supervisor(Gateway())
    try:
        supervisor.run()
        return 0
    finally:
        if supervisor.active_pairs:
            supervisor.stop_all("Automation service stopping")
        supervisor.phase = "stopped"
        supervisor.publish("Automation supervisor stopped")


if __name__ == "__main__":
    raise SystemExit(main())
