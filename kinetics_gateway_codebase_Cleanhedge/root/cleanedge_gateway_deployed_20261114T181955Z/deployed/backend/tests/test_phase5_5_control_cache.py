from __future__ import annotations

import threading

from app.services.bms_pcs_control import BmsPcsControlService
from app.services.gateway_service import GatewayService


def test_control_snapshot_contract_is_bounded() -> None:
    keys = BmsPcsControlService.control_snapshot_point_keys()
    assert set(keys) == {"bank", "rack", "pcs"}
    assert "active_power_setpoint" in keys["pcs"]
    assert "grid_active_power" in keys["pcs"]
    assert "operating_state" in keys["pcs"]
    assert "pre_charge_state" in keys["rack"]
    assert "auto_onnected" in keys["bank"]
    assert sum(map(len, keys.values())) < 50


def test_control_pair_snapshot_does_not_take_main_snapshot_lock() -> None:
    service = GatewayService.__new__(GatewayService)
    service._lock = threading.Lock()
    service._control_cache_lock = threading.Lock()
    service._control_pair_cache = {
        "pair_1": {
            "sequence": 7,
            "bank": {"telemetry": {}},
            "rack": {"telemetry": {}},
            "pcs": {"telemetry": {"grid_active_power": {"value": 0.0}}},
        }
    }
    service.config = type(
        "Config",
        (),
        {
            "control_sequence": type(
                "Control",
                (),
                {
                    "pairs": [
                        type(
                            "Pair",
                            (),
                            {
                                "pair_id": "pair_1",
                                "rack_id": 1,
                                "pcs_asset_id": "pcs_1",
                            },
                        )()
                    ]
                },
            )()
        },
    )()

    assert service._lock.acquire(blocking=False)
    try:
        result = service.control_pair_snapshot(1, "pcs_1")
    finally:
        service._lock.release()

    assert result["sequence"] == 7
    result["pcs"]["telemetry"].clear()
    assert service._control_pair_cache["pair_1"]["pcs"]["telemetry"]
