import asyncio
from types import SimpleNamespace

from app.central_sync.charge_discharge_producer import ChargeDischargeProducer
from app.central_sync.cleanedge_normalized import build_cleanedge_normalized_snapshot
from app.central_sync.general_asset_producer import GeneralAssetProducer, normalize_runtime_point


def point(value, unit, raw=None):
    return {"value": value, "raw": value if raw is None else raw, "unit": unit, "quality": "good"}


def test_rack_normalization_is_unit_qualified():
    snapshot = {
        "gateway_id": "g1", "timestamp": "2026-01-01T00:00:00+00:00", "sequence": 1,
        "racks": [{
            "asset_id": "bms_rack_1", "rack_id": 1, "online": True, "timestamp": "2026-01-01T00:00:00+00:00",
            "telemetry": {
                "soc": point(523, "‰"), "soh": point(1000, "‰"),
                "vcell_max": point(3293, "mV"), "vcell_min": point(3291, "mV"),
                "ir_pos": point(20000, "kΩ"), "ir_neg": point(20000, "kΩ"),
                "irack_chg_limit": point(178, "A"), "irack_dsg_limit": point(177, "A"),
            },
        }],
        "pcs_devices": {"pcs_1": {"asset_id": "pcs_1", "online": True, "telemetry": {}}},
        "bank": {"asset_id": "bms_bank", "online": True, "telemetry": {
            "soc": point(52.3, "%", 523), "bank_soh": point(100.0, "%", 1000),
        }},
    }
    result = build_cleanedge_normalized_snapshot(snapshot)
    rack = result["battery_racks"]["1"]
    assert rack["soc_percent"] == 52.3
    assert rack["soh_percent"] == 100.0
    assert rack["max_cell_voltage_v"] == 3.293
    assert rack["positive_insulation_mohm"] == 20.0
    assert rack["max_charge_current_a"] == 178.0
    assert result["battery_system"]["soc_percent"] == 52.3


def test_s2_runtime_conversions_and_single_bank_asset():
    rack = {"asset_type": "bms_rack", "telemetry": {}}
    converted = normalize_runtime_point(rack, "rack_inner_soc", point(525, "‰"))
    assert converted["value"] == 52.5
    assert converted["unit"] == "%"
    meter = {"asset_type": "energy_meter"}
    converted = normalize_runtime_point(meter, "essmeter_1_27", point(1112011779, "Hz"))
    assert abs(converted["value"] - 49.9882926940918) < 1e-6
    assets = list(GeneralAssetProducer._assets({
        "bank": {"asset_id": "bms_bank", "telemetry": {}},
        "bms_banks": {"b1": {"asset_id": "bms_bank_1", "telemetry": {}}},
        "racks": [], "environment": {}, "pcs_devices": {},
    }))
    assert [a["asset_id"] for a in assets] == ["bms_bank"]


class FakeApi:
    async def control_status_compact(self):
        return {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "pairs": [{
                "pair": {"pair_id": "pair_1", "rack_id": 1, "pcs_asset_id": "pcs_1", "enabled": True},
                "runtime": {"stage": "complete", "run_status": "success", "requested_direction": "discharge", "requested_power_kw": 5.0, "commanded_power_kw": 5.0},
                "summary": {"pcs_actual_power_kw": 4.8, "rack_voltage_v": 1370.7, "contactors_ready": True},
                "workflow": {"ready_for_power": True, "hard_blocked": False}, "errors": [],
            }],
            "summary": {"parallel_operation_supported": True, "active_pairs": ["pair_1"]},
        }

    async def platform_readiness(self):
        return {"ready": True, "commissioning": "complete"}

    async def normalized_snapshot(self):
        return {"pairs": {"pair_1": {"battery": {"soc_percent": 52.3}, "pcs": {"running": True}}}}


class FakeOutbox:
    def next_sequence(self, stream, substream):
        return 1

    def enqueue(self, message):
        self.message = message
        return True


def test_s10_cleanedge_nested_pair_shape():
    config = SimpleNamespace(
        charge_discharge=SimpleNamespace(
            enabled=True, stream="battery_charge_discharge_control", substream="pair_controller",
            priority="P1", poll_interval_sec=1.0, heartbeat_interval_sec=5.0, status_file="/tmp/no-write/status.json",
        ),
        identity=SimpleNamespace(schema_version="1.0", gateway_id="g1", site_id="cleanedge", organization_id="org", software_version="v"),
        backend=SimpleNamespace(gzip_enabled=False),
    )
    outbox = FakeOutbox()
    producer = ChargeDischargeProducer(config=config, outbox=outbox, local_api=FakeApi())
    result = asyncio.run(producer.collect_once(force_emit=True))
    assert result["record_count"] == 1
    record = outbox.message.records[0]
    assert record["pair_id"] == "pair_1"
    assert record["run_status"] == "success"
    assert record["rack_soc_percent"] == 52.3
    assert record["actual_power_kw"] == 4.8
    assert record["hard_blocked"] is False
