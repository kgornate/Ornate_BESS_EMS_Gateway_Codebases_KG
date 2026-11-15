from pathlib import Path

import pytest

from app.services.automation_schedule import AutomationScheduleStore, default_config, validate_config


def test_default_is_safe_and_disabled():
    config = default_config()
    assert config["enabled"] is False
    assert config["solar"] == {
        "start_above_kw": 100.0,
        "start_hold_seconds": 60,
        "stop_below_kw": 80.0,
        "stop_hold_seconds": 30,
        "max_age_seconds": 15,
    }


def test_rejects_overlapping_windows_and_invalid_hysteresis():
    config = default_config()
    config["charge"] = {"start": "09:00", "stop": "15:30"}
    with pytest.raises(ValueError, match="must not overlap"):
        validate_config(config)
    config = default_config()
    config["solar"]["stop_below_kw"] = 110
    with pytest.raises(ValueError, match="greater"):
        validate_config(config)


def test_atomic_store_round_trip(tmp_path: Path):
    store = AutomationScheduleStore(tmp_path / "config.json", tmp_path / "status.json")
    config = default_config()
    config["enabled"] = True
    config["pairs"]["pair_2"]["charge_kw"] = 17.5
    saved = store.put_config(config, "internal")
    assert store.get_config()["pairs"]["pair_2"]["charge_kw"] == 17.5
    assert saved["updated_by"] == "internal"
