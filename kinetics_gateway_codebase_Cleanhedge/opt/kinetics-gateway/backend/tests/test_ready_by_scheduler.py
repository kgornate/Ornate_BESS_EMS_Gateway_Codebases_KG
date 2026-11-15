from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCHEDULER = Path("/root/bess_solar_time_automation.py")
IST = ZoneInfo("Asia/Kolkata")


def _module():
    spec = importlib.util.spec_from_file_location("ready_by_scheduler", SCHEDULER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config() -> dict:
    return {
        "enabled": True,
        "days": [1, 2, 3, 4, 5, 6, 7],
        "charge": {"start": "09:00", "stop": "14:30"},
        "discharge": {"start": "15:00", "stop": "17:00"},
        "timing": {
            "preparation_lead_minutes": 15,
            "power_dispatch_lead_seconds": 120,
        },
    }


def _at(clock: str) -> datetime:
    return datetime.fromisoformat(f"2026-11-10T{clock}").replace(tzinfo=IST)


def test_charge_ready_by_boundaries() -> None:
    scheduler = _module()
    assert scheduler.schedule_plan(_config(), _at("08:44:59")) is None
    preparing = scheduler.schedule_plan(_config(), _at("08:45:00"))
    assert preparing["direction"] == "charge"
    assert preparing["mode"] == "prepare"
    dispatching = scheduler.schedule_plan(_config(), _at("08:58:00"))
    assert dispatching["mode"] == "operate"
    assert dispatching["active_by"] == _at("09:00:00")
    assert scheduler.schedule_plan(_config(), _at("14:30:00")) is None


def test_discharge_ready_by_boundaries() -> None:
    scheduler = _module()
    preparing = scheduler.schedule_plan(_config(), _at("14:45:00"))
    assert preparing["direction"] == "discharge"
    assert preparing["mode"] == "prepare"
    dispatching = scheduler.schedule_plan(_config(), _at("14:58:00"))
    assert dispatching["mode"] == "operate"
    assert dispatching["active_by"] == _at("15:00:00")
    assert scheduler.schedule_plan(_config(), _at("17:00:00")) is None
