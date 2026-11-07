from __future__ import annotations

from app.assets.solar_driver import SolarModbusDriver
from app.core.config import SolarConfig


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def read_registers(self, unit_id, address, count, function_code, *, priority):
        self.calls.append((unit_id, address, count, function_code))
        if address == 2100:
            words = [0] * 26
            words[10:12] = [31049, 18846]
            words[14:16] = [0, 16256]
            return words
        words = [0] * 23
        words[0:4] = [7, 13, 0, 0]
        return words

    def close(self):
        pass


def test_phase3b_decodes_field_word_order_and_stays_dispatch_gated():
    driver = SolarModbusDriver(SolarConfig(enabled=True))
    driver.client = FakeClient()
    asset = driver.read_all()
    telemetry = asset["telemetry"]
    assert driver.client.calls == [(1, 2100, 26, 3), (1, 2600, 23, 3)]
    assert telemetry["meter_ct_ratio"]["value"] == 1.0
    assert telemetry["total_site_load_energy_kwh"]["value"] == 1298217.125
    assert telemetry["inverters_online"]["value"] == 7
    assert telemetry["solar_dispatch_ready"]["value"] is False
    assert telemetry["solar_surplus_available_kw"]["value"] is None


def test_phase3b_can_expose_validated_export_as_surplus_without_writes():
    driver = SolarModbusDriver(SolarConfig(enabled=True, dispatch_validation_complete=True))
    driver.client = FakeClient()
    asset = driver.read_all()
    assert asset["telemetry"]["solar_dispatch_ready"]["value"] is True
    assert asset["telemetry"]["solar_surplus_available_kw"]["value"] == 0.0
