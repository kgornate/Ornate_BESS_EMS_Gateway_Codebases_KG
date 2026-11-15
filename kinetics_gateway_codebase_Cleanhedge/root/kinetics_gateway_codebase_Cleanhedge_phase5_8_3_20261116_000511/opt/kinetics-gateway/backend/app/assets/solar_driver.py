from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import Any

from app.core.config import SolarConfig
from app.protocols.io_arbiter import IoPriority
from app.protocols.modbus_tcp import ModbusTcpClient


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SolarModbusDriver:
    """Read-only HopeComBox solar plus ABB ACDB grid-meter driver."""

    def __init__(self, config: SolarConfig) -> None:
        self.config = config
        self.client = ModbusTcpClient(
            config.host, config.port, config.timeout_seconds, source_ip=config.source_ip
        )
        self.grid_client = ModbusTcpClient(
            config.grid_meter.host,
            config.grid_meter.port,
            config.grid_meter.timeout_seconds,
            source_ip=config.grid_meter.source_ip,
        )

    def close(self) -> None:
        self.client.close()
        self.grid_client.close()

    def _u32(self, words: list[int]) -> int:
        ordered = [words[1], words[0]] if self.config.word_order == "low_high" else words
        return (ordered[0] << 16) | ordered[1]

    def _i32(self, words: list[int]) -> int:
        value = self._u32(words)
        return value - 0x100000000 if value & 0x80000000 else value

    def _f32(self, words: list[int]) -> float:
        ordered = [words[1], words[0]] if self.config.word_order == "low_high" else words
        return struct.unpack(">f", struct.pack(">HH", *ordered))[0]

    def _grid_f32(self, words: list[int]) -> float:
        ordered = (
            [words[1], words[0]]
            if self.config.grid_meter.word_order == "low_high"
            else words
        )
        return struct.unpack(">f", struct.pack(">HH", *ordered))[0]

    @staticmethod
    def _point(value: Any, unit: str | None, address: int, raw: list[int]) -> dict[str, Any]:
        return {"value": value, "unit": unit, "quality": "good", "address": str(address), "raw": raw}

    def read_all(self) -> dict[str, Any]:
        meter = self.client.read_registers(
            self.config.unit_id, 2100, 26, 3, priority=IoPriority.TELEMETRY
        )
        aggregate = self.client.read_registers(
            self.config.unit_id, 2600, 23, 3, priority=IoPriority.TELEMETRY
        )
        t: dict[str, Any] = {}
        float_points = (
            ("pcc_export_power_kw", 2100, 0, "kW"),
            ("grid_import_power_kw", 2102, 2, "kW"),
            ("site_load_power_kw", 2104, 4, "kW"),
            ("total_export_energy_kwh", 2106, 6, "kWh"),
            ("total_grid_import_energy_kwh", 2108, 8, "kWh"),
            ("total_site_load_energy_kwh", 2110, 10, "kWh"),
            ("meter_power_factor", 2112, 12, None),
            ("meter_ct_ratio", 2114, 14, None),
            ("meter_phase_a_power_kw", 2118, 18, "kW"),
            ("meter_phase_b_power_kw", 2120, 20, "kW"),
            ("meter_phase_c_power_kw", 2122, 22, "kW"),
            ("meter_pt_ratio", 2124, 24, None),
        )
        for key, address, offset, unit in float_points:
            raw = meter[offset:offset + 2]
            t[key] = self._point(round(self._f32(raw), 6), unit, address, raw)
        t["zero_export_control_status"] = self._point(meter[16], None, 2116, [meter[16]])
        t["meter_communication_status"] = self._point(meter[17], None, 2117, [meter[17]])
        for key, offset in (("inverters_online", 0), ("inverters_offline", 1),
                            ("inverters_on_grid", 2), ("inverters_faulted", 3)):
            t[key] = self._point(aggregate[offset], None, 2600 + offset, [aggregate[offset]])
        for key, address, offset, signed, scale, unit in (
            ("solar_active_power_kw", 2604, 4, False, 100.0, "kW"),
            ("solar_reactive_power_kvar", 2606, 6, True, 100.0, "kVar"),
            ("daily_yield", 2608, 8, False, 100000.0, None),
            ("total_yield_kwh", 2610, 10, False, 100000.0, "kWh"),
            ("actual_active_regulation_kw", 2612, 12, False, 100.0, "kW"),
            ("reactive_regulation_kvar", 2614, 14, True, 10.0, "kVar"),
            ("rated_power_kw", 2616, 16, False, 1.0, "kW"),
        ):
            raw = aggregate[offset:offset + 2]
            value = self._i32(raw) if signed else self._u32(raw)
            t[key] = self._point(round(value / scale, 6), unit, address, raw)
        for key, offset in (("array_operation_status", 18), ("array_running", 19),
                            ("array_shutdown", 20), ("array_fault_or", 21),
                            ("array_fault_and", 22)):
            t[key] = self._point(aggregate[offset], None, 2600 + offset, [aggregate[offset]])

        if self.config.grid_meter.enabled:
            # ABB M1M ACDB meter: FC03, documented registers 40101 onward.
            # Protocol offsets are zero based, so 40101 is requested as 100.
            grid_main = self.grid_client.read_registers(
                self.config.grid_meter.unit_id,
                100,
                58,
                3,
                priority=IoPriority.TELEMETRY,
            )
            grid_energy = self.grid_client.read_registers(
                self.config.grid_meter.unit_id,
                158,
                26,
                3,
                priority=IoPriority.TELEMETRY,
            )

            # Preserve the HopeComBox's uncommissioned PCC fields for diagnostics,
            # while making the ABB meter authoritative for the public fields.
            for key in (
                "pcc_export_power_kw",
                "grid_import_power_kw",
                "site_load_power_kw",
                "total_export_energy_kwh",
                "total_grid_import_energy_kwh",
                "total_site_load_energy_kwh",
                "meter_power_factor",
                "meter_communication_status",
            ):
                t[f"hopewind_reported_{key}"] = dict(t[key])

            active_kw = self._grid_f32(grid_main[0:2]) / 1000.0
            directed_kw = (
                active_kw
                if self.config.grid_meter.positive_power_is_export
                else -active_kw
            )
            export_kw = max(0.0, directed_kw)
            import_kw = max(0.0, -directed_kw)
            solar_kw = float(t["solar_active_power_kw"]["value"])
            calculated_load_kw = solar_kw - export_kw + import_kw

            t["pcc_export_power_kw"] = self._point(
                round(export_kw, 6), "kW", 40101, grid_main[0:2]
            )
            t["grid_import_power_kw"] = self._point(
                round(import_kw, 6), "kW", 40101, grid_main[0:2]
            )
            t["site_load_power_kw"] = self._point(
                round(calculated_load_kw, 6), "kW", 40101, grid_main[0:2]
            )
            t["total_export_energy_kwh"] = self._point(
                round(abs(self._grid_f32(grid_energy[0:2])) / 1000.0, 6),
                "kWh",
                40159,
                grid_energy[0:2],
            )
            t["total_grid_import_energy_kwh"] = self._point(
                round(abs(self._grid_f32(grid_energy[8:10])) / 1000.0, 6),
                "kWh",
                40167,
                grid_energy[8:10],
            )
            t["meter_power_factor"] = self._point(
                round(self._grid_f32(grid_main[16:18]), 6),
                None,
                40117,
                grid_main[16:18],
            )
            t["grid_reactive_power_kvar"] = self._point(
                round(self._grid_f32(grid_main[8:10]) / 1000.0, 6),
                "kVar",
                40109,
                grid_main[8:10],
            )
            t["grid_apparent_power_kva"] = self._point(
                round(self._grid_f32(grid_main[24:26]) / 1000.0, 6),
                "kVA",
                40125,
                grid_main[24:26],
            )
            t["grid_line_voltage_v"] = self._point(
                round(self._grid_f32(grid_main[32:34]), 6),
                "V",
                40133,
                grid_main[32:34],
            )
            t["grid_phase_voltage_v"] = self._point(
                round(self._grid_f32(grid_main[40:42]), 6),
                "V",
                40141,
                grid_main[40:42],
            )
            t["grid_average_current_a"] = self._point(
                round(self._grid_f32(grid_main[48:50]), 6),
                "A",
                40149,
                grid_main[48:50],
            )
            t["grid_frequency_hz"] = self._point(
                round(self._grid_f32(grid_main[56:58]), 6),
                "Hz",
                40157,
                grid_main[56:58],
            )
            t["meter_communication_status"] = self._point(
                0, None, 40101, grid_main[0:2]
            )
            t["grid_meter_source"] = self._point(
                "abb_acdb_mfm", None, 40101, grid_main[0:2]
            )

        export_kw = float(t["pcc_export_power_kw"]["value"])
        grid_ready = (
            export_kw >= self.config.grid_meter.minimum_export_for_dispatch_kw
            if self.config.grid_meter.enabled
            else meter[17] != 2
        )
        # Installation does not enable dispatch. A later controlled field step
        # must explicitly set dispatch_validation_complete=true.
        t["solar_dispatch_ready"] = self._point(
            bool(self.config.dispatch_validation_complete and grid_ready),
            None,
            40101 if self.config.grid_meter.enabled else 2117,
            grid_main[0:2] if self.config.grid_meter.enabled else [meter[17]],
        )
        t["solar_surplus_available_kw"] = self._point(
            max(0.0, export_kw)
            if self.config.dispatch_validation_complete else None,
            "kW",
            40101 if self.config.grid_meter.enabled else 2100,
            grid_main[0:2] if self.config.grid_meter.enabled else meter[0:2],
        )
        return {
            "asset_id": "solar_plant", "asset_type": "solar_plant", "label": "Solar Plant / PCC",
            "unit_id": self.config.unit_id, "transport": "modbus_tcp", "online": True,
            "timestamp": now_iso(), "telemetry": t, "read_errors": [],
            "host": self.config.host, "port": self.config.port,
        }

    def io_arbiter_diagnostics(self) -> dict[str, Any]:
        diagnostics = {"shared_tcp": self.client.io_arbiter.snapshot()}
        if self.config.grid_meter.enabled:
            diagnostics["grid_meter"] = self.grid_client.io_arbiter.snapshot()
        return diagnostics
