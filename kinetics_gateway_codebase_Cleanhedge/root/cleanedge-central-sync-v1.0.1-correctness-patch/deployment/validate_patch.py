from app.central_sync.cleanedge_normalized import build_cleanedge_normalized_snapshot
from app.central_sync.general_asset_producer import GeneralAssetProducer, normalize_runtime_point


def point(value, unit, raw=None):
    return {"value": value, "raw": value if raw is None else raw, "unit": unit, "quality": "good"}


snapshot = {
    "gateway_id": "validation", "timestamp": "2026-01-01T00:00:00+00:00", "sequence": 1,
    "racks": [{"asset_id": "bms_rack_1", "rack_id": 1, "online": True, "telemetry": {
        "soc": point(523, "‰"), "soh": point(1000, "‰"),
        "vcell_max": point(3293, "mV"), "vcell_min": point(3291, "mV"),
        "ir_pos": point(20000, "kΩ"), "ir_neg": point(20000, "kΩ"),
        "irack_chg_limit": point(178, "A"), "irack_dsg_limit": point(177, "A"),
    }}],
    "pcs_devices": {"pcs_1": {"asset_id": "pcs_1", "online": True, "telemetry": {}}},
    "bank": {"asset_id": "bms_bank", "online": True, "telemetry": {
        "soc": point(52.3, "%", 523), "bank_soh": point(100.0, "%", 1000),
    }},
}
normalized = build_cleanedge_normalized_snapshot(snapshot)
rack = normalized["battery_racks"]["1"]
assert rack["soc_percent"] == 52.3
assert rack["soh_percent"] == 100.0
assert rack["max_cell_voltage_v"] == 3.293
assert rack["positive_insulation_mohm"] == 20.0
assert rack["max_charge_current_a"] == 178.0
assert normalized["battery_system"]["soc_percent"] == 52.3

s2 = normalize_runtime_point({"asset_type": "bms_rack"}, "rack_inner_soc", point(525, "‰"))
assert s2["value"] == 52.5 and s2["unit"] == "%"
meter = normalize_runtime_point({"asset_type": "energy_meter"}, "essmeter_1_27", point(1112011779, "Hz"))
assert abs(meter["value"] - 49.9882926940918) < 1e-6

assets = list(GeneralAssetProducer._assets({
    "bank": {"asset_id": "bms_bank", "telemetry": {}},
    "bms_banks": {"b1": {"asset_id": "bms_bank_1", "telemetry": {}}},
    "racks": [], "environment": {}, "pcs_devices": {},
}))
assert [item["asset_id"] for item in assets] == ["bms_bank"]
print("CleanEdge Central Sync correctness validation: PASS")

