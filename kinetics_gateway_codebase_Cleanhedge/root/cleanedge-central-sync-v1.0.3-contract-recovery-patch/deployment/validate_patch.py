from app.central_sync.general_asset_producer import classify_substream
from app.central_sync.uploader import (
    is_terminal_contract_rejection,
    should_retry_backend_rejection,
)


solar = {"asset_id": "solar_plant", "asset_type": "solar_plant"}
meter = {"asset_id": "grid_meter_1", "asset_type": "energy_meter", "vendor": "acrel"}

for signal in (
    "grid_active_power_kw",
    "pcc_export_power_kw",
    "total_grid_import_energy_kwh",
    "inverter_1_active_power_kw",
):
    assert classify_substream(solar, signal, None) == "solar_inverter"

for signal in ("grid_active_power_kw", "total_active_energy_kwh"):
    assert classify_substream(meter, signal, None) == "combiner_box_meter"

for code in (
    "substream_excluded",
    "stream_excluded",
    "unsupported_substream",
    "unsupported_stream",
):
    assert is_terminal_contract_rejection(code)

for code in ("rate_limited", "backend_busy", "temporary_failure", ""):
    assert not is_terminal_contract_rejection(code)

assert not should_retry_backend_rejection("substream_excluded", True)
assert not should_retry_backend_rejection("substream_excluded", False)
assert should_retry_backend_rejection("rate_limited", True)
assert not should_retry_backend_rejection("rate_limited", False)

print("CleanEdge Central Sync v1.0.3 contract/recovery validation: PASS")
