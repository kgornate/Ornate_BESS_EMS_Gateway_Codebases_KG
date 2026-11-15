from __future__ import annotations

import struct

from app.assets.solar_driver import SolarModbusDriver
from app.core.config import SolarConfig, SolarGridMeterConfig


def cdab_words(value: float) -> list[int]:
    high, low = struct.unpack(">HH", struct.pack(">f", value))
    return [low, high]


class FakeArbiter:
    def snapshot(self):
        return {"completed": 2, "timeouts": 0}


class FakeHopewindClient:
    def __init__(self) -> None:
        self.calls = []
        self.io_arbiter = FakeArbiter()

    def read_registers(self, unit_id, address, count, function_code, *, priority):
        self.calls.append((unit_id, address, count, function_code))
        if address == 2100:
            return [0] * 26
        words = [0] * 23
        words[0:4] = [7, 13, 7, 0]
        # low-word-first uint32, scaled by 100 => 135.30 kW
        words[4:6] = [13530, 0]
        return words

    def close(self):
        pass


class FakeAbbClient:
    def __init__(self, active_power_w: float = 110990.0) -> None:
        self.active_power_w = active_power_w
        self.calls = []
        self.io_arbiter = FakeArbiter()

    def read_registers(self, unit_id, address, count, function_code, *, priority):
        self.calls.append((unit_id, address, count, function_code))
        if address == 100:
            words = [0] * 58
            for index, value in {
                0: self.active_power_w,
                8: 62420.0,
                16: -0.872,
                24: 127340.0,
                32: 738.73,
                40: 426.50,
                48: 100.36,
                56: 50.28,
            }.items():
                words[index:index + 2] = cdab_words(value)
            return words
        words = [0] * 26
        words[0:2] = cdab_words(1127838850.0)
        words[8:10] = cdab_words(-1055810.0)
        return words

    def close(self):
        pass


def make_driver(active_power_w: float = 110990.0, validated: bool = False):
    config = SolarConfig(
        enabled=True,
        dispatch_validation_complete=validated,
        grid_meter=SolarGridMeterConfig(
            enabled=True,
            minimum_export_for_dispatch_kw=20.0,
        ),
    )
    driver = SolarModbusDriver(config)
    driver.client = FakeHopewindClient()
    driver.grid_client = FakeAbbClient(active_power_w)
    return driver


def test_abb_grid_meter_replaces_uncommissioned_hopewind_pcc_fields():
    driver = make_driver()
    telemetry = driver.read_all()["telemetry"]

    assert telemetry["solar_active_power_kw"]["value"] == 135.3
    assert telemetry["pcc_export_power_kw"]["value"] == 110.99
    assert telemetry["grid_import_power_kw"]["value"] == 0.0
    assert telemetry["site_load_power_kw"]["value"] == 24.31
    assert telemetry["grid_meter_source"]["value"] == "abb_acdb_mfm"
    assert telemetry["meter_communication_status"]["value"] == 0
    assert abs(telemetry["grid_frequency_hz"]["value"] - 50.28) < 0.001
    assert telemetry["solar_dispatch_ready"]["value"] is False
    assert telemetry["solar_surplus_available_kw"]["value"] is None
    assert "hopewind_reported_pcc_export_power_kw" in telemetry
    assert driver.grid_client.calls == [
        (1, 100, 58, 3),
        (1, 158, 26, 3),
    ]


def test_negative_abb_power_is_grid_import():
    driver = make_driver(active_power_w=-12500.0)
    telemetry = driver.read_all()["telemetry"]

    assert telemetry["pcc_export_power_kw"]["value"] == 0.0
    assert telemetry["grid_import_power_kw"]["value"] == 12.5
    assert telemetry["site_load_power_kw"]["value"] == 147.8
    assert telemetry["solar_dispatch_ready"]["value"] is False


def test_dispatch_needs_explicit_validation_and_minimum_live_export():
    driver = make_driver(active_power_w=25000.0, validated=True)
    telemetry = driver.read_all()["telemetry"]
    assert telemetry["solar_dispatch_ready"]["value"] is True
    assert telemetry["solar_surplus_available_kw"]["value"] == 25.0

    driver = make_driver(active_power_w=15000.0, validated=True)
    telemetry = driver.read_all()["telemetry"]
    assert telemetry["solar_dispatch_ready"]["value"] is False
    assert telemetry["solar_surplus_available_kw"]["value"] == 15.0


def test_io_diagnostics_exposes_both_modbus_links():
    diagnostics = make_driver().io_arbiter_diagnostics()
    assert diagnostics["shared_tcp"]["timeouts"] == 0
    assert diagnostics["grid_meter"]["timeouts"] == 0
