from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
PAIR_IDS = ("pair_1", "pair_2", "pair_3", "pair_4")
DEFAULT_CONFIG_PATH = Path(
    os.getenv(
        "KINETICS_AUTOMATION_CONFIG",
        "/mnt/ems-logs/kinetics-gateway/automation_schedule.json",
    )
)
DEFAULT_STATUS_PATH = Path(
    os.getenv(
        "KINETICS_AUTOMATION_STATUS",
        "/mnt/ems-logs/kinetics-gateway/automation_status.json",
    )
)


def default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "enabled": False,
        "timezone": "Asia/Kolkata",
        "days": [1, 2, 3, 4, 5, 6, 7],
        "charge": {"start": "09:00", "stop": "14:30"},
        "discharge": {"start": "15:00", "stop": "17:00"},
        "solar": {
            "start_above_kw": 100.0,
            "start_hold_seconds": 60,
            "stop_below_kw": 80.0,
            "stop_hold_seconds": 30,
            "max_age_seconds": 15,
        },
        "timing": {
            "preparation_lead_minutes": 15,
            "power_dispatch_lead_seconds": 120,
        },
        "pairs": {
            pair_id: {
                "enabled": True,
                "charge_kw": 5.0,
                "discharge_kw": 5.0,
            }
            for pair_id in PAIR_IDS
        },
        "updated_at": None,
        "updated_by": None,
    }


def _hhmm(value: Any, label: str) -> str:
    text = str(value or "")
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError as error:
        raise ValueError(f"{label} must use HH:MM (24-hour) format") from error
    return parsed.strftime("%H:%M")


def validate_config(payload: dict[str, Any]) -> dict[str, Any]:
    base = default_config()
    result = deepcopy(base)
    result["enabled"] = bool(payload.get("enabled", False))
    result["timezone"] = "Asia/Kolkata"
    days = sorted({int(day) for day in payload.get("days", base["days"])})
    if not days or any(day < 1 or day > 7 for day in days):
        raise ValueError("days must contain ISO weekdays 1 through 7")
    result["days"] = days

    intervals: list[tuple[int, int, str]] = []
    for direction in ("charge", "discharge"):
        source = payload.get(direction) or {}
        start = _hhmm(source.get("start", base[direction]["start"]), f"{direction}.start")
        stop = _hhmm(source.get("stop", base[direction]["stop"]), f"{direction}.stop")
        start_min = int(start[:2]) * 60 + int(start[3:])
        stop_min = int(stop[:2]) * 60 + int(stop[3:])
        if start_min >= stop_min:
            raise ValueError(f"{direction} stop must be later than start on the same day")
        result[direction] = {"start": start, "stop": stop}
        intervals.append((start_min, stop_min, direction))
    intervals.sort()
    if intervals[0][1] > intervals[1][0]:
        raise ValueError("charge and discharge windows must not overlap")

    solar = payload.get("solar") or {}
    start_kw = float(solar.get("start_above_kw", 100.0))
    stop_kw = float(solar.get("stop_below_kw", 80.0))
    start_hold = int(solar.get("start_hold_seconds", 60))
    stop_hold = int(solar.get("stop_hold_seconds", 30))
    max_age = int(solar.get("max_age_seconds", 15))
    if start_kw <= stop_kw:
        raise ValueError("solar start threshold must be greater than stop threshold")
    if start_hold < 1 or stop_hold < 1 or max_age < 1:
        raise ValueError("solar hold and maximum-age values must be positive")
    result["solar"] = {
        "start_above_kw": start_kw,
        "start_hold_seconds": start_hold,
        "stop_below_kw": stop_kw,
        "stop_hold_seconds": stop_hold,
        "max_age_seconds": max_age,
    }

    timing = payload.get("timing") or {}
    preparation_lead = int(timing.get("preparation_lead_minutes", 15))
    dispatch_lead = int(timing.get("power_dispatch_lead_seconds", 120))
    if not 5 <= preparation_lead <= 60:
        raise ValueError("preparation lead must be between 5 and 60 minutes")
    if not 0 <= dispatch_lead <= 300:
        raise ValueError("power dispatch lead must be between 0 and 300 seconds")
    if dispatch_lead >= preparation_lead * 60:
        raise ValueError("power dispatch lead must be shorter than preparation lead")
    result["timing"] = {
        "preparation_lead_minutes": preparation_lead,
        "power_dispatch_lead_seconds": dispatch_lead,
    }

    pairs = payload.get("pairs") or {}
    for pair_id in PAIR_IDS:
        source = pairs.get(pair_id) or base["pairs"][pair_id]
        charge_kw = float(source.get("charge_kw", 5.0))
        discharge_kw = float(source.get("discharge_kw", 5.0))
        if not 0 < charge_kw <= 250 or not 0 < discharge_kw <= 250:
            raise ValueError(f"{pair_id} power must be above 0 and at most 250 kW")
        result["pairs"][pair_id] = {
            "enabled": bool(source.get("enabled", True)),
            "charge_kw": charge_kw,
            "discharge_kw": discharge_kw,
        }
    if result["enabled"] and not any(item["enabled"] for item in result["pairs"].values()):
        raise ValueError("at least one pair must be enabled")
    return result


class AutomationScheduleStore:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH, status_path: Path = DEFAULT_STATUS_PATH):
        self.config_path = Path(config_path)
        self.status_path = Path(status_path)
        self._lock = threading.RLock()

    @staticmethod
    def _read(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else deepcopy(fallback)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return deepcopy(fallback)

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return validate_config(self._read(self.config_path, default_config()))

    def put_config(self, payload: dict[str, Any], username: str) -> dict[str, Any]:
        config = validate_config(payload)
        config["updated_at"] = datetime.now(IST).isoformat()
        config["updated_by"] = username
        with self._lock:
            self._write(self.config_path, config)
        return config

    def get_status(self) -> dict[str, Any]:
        fallback = {
            "version": 1,
            "phase": "disabled",
            "running": False,
            "timestamp": None,
            "message": "Automation supervisor has not reported yet",
            "active_pairs": [],
        }
        with self._lock:
            return self._read(self.status_path, fallback)


automation_schedule_store = AutomationScheduleStore()
