from __future__ import annotations

from typing import Any


def _point(asset: dict[str, Any] | None, *keys: str) -> dict[str, Any] | None:
    telemetry = (asset or {}).get("telemetry") or {}
    for key in keys:
        value = telemetry.get(key)
        if isinstance(value, dict):
            return value
    return None


def _value(asset: dict[str, Any] | None, *keys: str) -> Any:
    point = _point(asset, *keys)
    return None if point is None else point.get("value")


def _number(asset: dict[str, Any] | None, *keys: str) -> float | None:
    value = _value(asset, *keys)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _number_as(
    asset: dict[str, Any] | None,
    *keys: str,
    source_unit: str,
    divisor: float,
) -> float | None:
    """Convert only when the runtime point declares the expected source unit.

    CleanEdge bank values are already engineering values while rack SOC/SOH,
    cell voltage and insulation values use different engineering units.  Unit
    qualification prevents accidental double scaling.
    """
    point = _point(asset, *keys)
    if point is None:
        return None
    try:
        value = float(point.get("value"))
    except (TypeError, ValueError):
        return None
    return value / divisor if str(point.get("unit") or "") == source_unit else value


def _int(asset: dict[str, Any] | None, *keys: str) -> int | None:
    value = _value(asset, *keys)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _bit(asset: dict[str, Any] | None, key: str, bit: int) -> bool | None:
    point = _point(asset, key)
    if point is None:
        return None
    raw = point.get("raw", point.get("value"))
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    try:
        return bool((int(raw) >> bit) & 1)
    except (TypeError, ValueError):
        return None


def _rack_view(asset: dict[str, Any]) -> dict[str, Any]:
    contactor = _point(asset, "contactor_state") or {}
    fields = contactor.get("bitfields") or {}
    pos = fields.get("positive_contactor_state")
    neg = fields.get("negative_contactor_state")
    return {
        "asset_id": asset.get("asset_id"),
        "rack_id": asset.get("rack_id"),
        "online": bool(asset.get("online", False)),
        "timestamp": asset.get("timestamp"),
        "state_code": _int(asset, "bcu_run_state"),
        "state": None,
        "voltage_v": _number(asset, "vrack", "rack_voltage"),
        "current_a": _number(asset, "irack", "rack_current"),
        "soc_percent": _number_as(asset, "soc", "rack_inner_soc", source_unit="‰", divisor=10.0),
        "soh_percent": _number_as(asset, "soh", source_unit="‰", divisor=10.0),
        "max_charge_current_a": _number(asset, "irack_chg_limit", "irack_chg_lim"),
        "max_discharge_current_a": _number(asset, "irack_dsg_limit", "irack_dsg_lim"),
        "positive_contactor_closed": None if pos is None else bool(int(pos)),
        "negative_contactor_closed": None if neg is None else bool(int(neg)),
        "positive_insulation_mohm": _number_as(asset, "ir_pos", source_unit="kΩ", divisor=1000.0),
        "negative_insulation_mohm": _number_as(asset, "ir_neg", source_unit="kΩ", divisor=1000.0),
        "max_cell_voltage_v": _number_as(asset, "vcell_max", source_unit="mV", divisor=1000.0),
        "min_cell_voltage_v": _number_as(asset, "vcell_min", source_unit="mV", divisor=1000.0),
        "max_cell_temperature_c": _number(asset, "tcell_max"),
        "min_cell_temperature_c": _number(asset, "tcell_min"),
        "charge_allowed": _number(asset, "irack_chg_limit", "irack_chg_lim") is not None,
        "discharge_allowed": _number(asset, "irack_dsg_limit", "irack_dsg_lim") is not None,
        "blocking_reasons": [] if asset.get("online") else ["communication_offline"],
    }


def _pcs_view(asset: dict[str, Any]) -> dict[str, Any]:
    operating = _int(asset, "operating_state")
    running = None if operating is None else bool(operating & 0x0010 or operating & 0x0040)
    power = _number(asset, "grid_active_power")
    return {
        "asset_id": asset.get("asset_id"),
        "label": asset.get("label"),
        "online": bool(asset.get("online", False)),
        "timestamp": asset.get("timestamp"),
        "running": running,
        "charging": None if power is None else power < 0,
        "discharging": None if power is None else power > 0,
        "faulted": _bit(asset, "status_word_2", 10),
        "standby": None if operating is None else bool(operating & 0x0008),
        "shutdown": None if operating is None else bool(operating & 0x0001),
        "epo": None,
        "off_grid": None,
        "dc_precharge_connected": _bit(asset, "status_word_2", 5),
        "dc_relay_connected": None if _bit(asset, "status_word_2", 6) is None else not _bit(asset, "status_word_2", 6),
        "ac_relay_connected": None,
        "active_power_kw": power,
        "reactive_power_kvar": _number(asset, "grid_reactive_power"),
        "power_factor": _number(asset, "power_factor"),
        "dc_voltage_v": _number(asset, "battery_voltage"),
        "dc_current_a": _number(asset, "battery_current"),
        "dc_power_kw": _number(asset, "dc_power"),
        "dc_bus_voltage_v": _number(asset, "dc_bus_voltage"),
        "grid_frequency_hz": _number(asset, "grid_frequency"),
        "grid_voltage_ab_v": _number(asset, "grid_ab_voltage"),
        "grid_voltage_bc_v": _number(asset, "grid_bc_voltage"),
        "grid_voltage_ca_v": _number(asset, "grid_ca_voltage"),
        "raw_status": operating,
    }


def build_cleanedge_normalized_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only normalized view only from the CleanEdge runtime cache."""
    racks = {
        str(item.get("rack_id")): _rack_view(item)
        for item in snapshot.get("racks", [])
        if isinstance(item, dict) and item.get("rack_id") is not None
    }
    pcs_assets = snapshot.get("pcs_devices") if isinstance(snapshot.get("pcs_devices"), dict) else {}
    pcs = {asset_id: _pcs_view(asset) for asset_id, asset in pcs_assets.items() if isinstance(asset, dict)}
    pairs: dict[str, Any] = {}
    for index, rack_id in enumerate(sorted(racks, key=lambda x: int(x)), start=1):
        pcs_id = f"pcs_{index}"
        if pcs_id not in pcs:
            continue
        battery = racks[rack_id]
        pairs[f"pair_{index}"] = {
            "pair_id": f"pair_{index}", "enabled": True, "rack_id": int(rack_id),
            "pcs_asset_id": pcs_id, "online": bool(battery.get("online") and pcs[pcs_id].get("online")),
            "battery": battery, "pcs": pcs[pcs_id],
        }
    bank = snapshot.get("bank") if isinstance(snapshot.get("bank"), dict) else {}
    battery_system = {
        "asset_id": bank.get("asset_id", "bms_bank"), "online": bool(bank.get("online")),
        "timestamp": bank.get("timestamp"), "voltage_v": _number(bank, "bank_voltage"),
        "current_a": _number(bank, "ibank"), "soc_percent": _number(bank, "soc"),
        "soh_percent": _number(bank, "bank_soh"),
        "max_cell_voltage_v": _number(bank, "vcell_max"), "min_cell_voltage_v": _number(bank, "vcell_min"),
        "max_cell_temperature_c": _number(bank, "tcell_max"), "min_cell_temperature_c": _number(bank, "tcell_min"),
    }
    return {
        "gateway_id": snapshot.get("gateway_id"), "timestamp": snapshot.get("timestamp"),
        "sequence": snapshot.get("sequence"), "vendors": {"bms": "kinetics", "pcs": "kinetics"},
        "battery_system": battery_system, "battery_racks": racks, "pcs": pcs, "pairs": pairs,
        "auxiliary": snapshot.get("environment") or {}, "solar": snapshot.get("solar") or {},
    }
