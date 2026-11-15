from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import GatewayConfig
from app.services.bms_pcs_control import BmsPcsControlService


def point(value, *, raw=None, bitfields=None):
    item = {"value": value, "raw": value if raw is None else raw, "quality": "good"}
    if bitfields is not None:
        item["bitfields"] = bitfields
    return item


def snapshot(*, stale=False, setpoint=200.0):
    stamp = datetime.now(timezone.utc) - (timedelta(seconds=60) if stale else timedelta(seconds=0.2))
    ts = stamp.isoformat()
    quality = {"timestamp": ts, "online": True, "read_errors": [], "point_count": 20}
    return {
        "sequence": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bank": {
            "online": True,
            "poll_status": {"fast": deepcopy(quality)},
            "telemetry": {
                "external_fault_state": point(0, raw=0, bitfields={
                    "emergency_stop_fault": 0,
                    "system_fault": 0,
                    "system_full": 0,
                    "system_empty": 0,
                    "pcs_ctrl": 0,
                }),
                "external_level1_alarm": point(0, raw=0),
                "bcu_external_lv1_alm_sum_ii": point(0, raw=0),
                "rack_fault": point(0, raw=0),
                "fault_info_sum2": point(0, raw=0),
                "rack_enable_state_l16": point(1, raw=1),
            },
        },
        "rack": {
            "rack_id": 1,
            "online": True,
            "poll_status": {"fast": deepcopy(quality), "normal": deepcopy(quality)},
            "telemetry": {
                "bcu_external_fault_alarm": point(0, raw=0),
                "external_critical": point(0, raw=0),
                "external_stop_alarm2": point(0, raw=0),
                "bcu_run_state": point(1),
                "vrack": point(1300.0),
                "irack_chg_limit": point(200.0),
                "irack_dsg_limit": point(200.0),
                "ir": point(1000.0),
                "ir_pos": point(1000.0),
                "ir_neg": point(1000.0),
                "pre_charge_state": point(3),
                "contactor_state": point(5, bitfields={
                    "positive_contactor_state": 1,
                    "negitive_contactor_state": 1,
                }),
            },
        },
        "pcs": {
            "asset_id": "pcs_1",
            "online": True,
            "poll_status": {"pcs": deepcopy(quality)},
            "telemetry": {
                "dc_bus_voltage": point(1300.0),
                "battery_voltage": point(1300.0),
                "operating_state": point(80),
                "status_word_1": point(3),
                "status_word_2": point(64),
                "reg_1210": point(128),
                "reg_121a": point(0),
                "grid_active_power": point(setpoint),
                "active_power_setpoint": point(setpoint),
            },
        },
    }


def service_for(provider):
    config_path = Path(os.environ.get("KINETICS_CONFIG", "/etc/kinetics-gateway/config.json"))
    cfg = GatewayConfig.model_validate(json.loads(config_path.read_text()))
    return BmsPcsControlService(cfg, object(), object(), object(), snapshot_provider=provider)


healthy = snapshot()
service = service_for(lambda rack_id, pcs_id: deepcopy(healthy))
result = service.runtime_monitor_sample("pair_1")
assert result["refresh"]["source"] == "background_poll_cache"
assert result["errors"] == []
assert result["summary"]["contactors_ready"] is True
assert result["summary"]["pcs_dc_breaker_feedback_closed"] is True
assert result["summary"]["bms_discharge_power_limit_kw"] == 260.0

# Healthy cache must never request a direct hardware status refresh.
def direct_read_forbidden(*args, **kwargs):
    raise AssertionError("healthy runtime cache unexpectedly requested live hardware")

service.status = direct_read_forbidden
assert service._runtime_monitor_status("pair_1", service._pair("pair_1"))["refresh"]["source"] == "background_poll_cache"

# Stale cache must perform exactly one fail-safe live confirmation attempt.
stale = snapshot(stale=True)
service = service_for(lambda rack_id, pcs_id: deepcopy(stale))
calls = {"count": 0}
live = service_for(lambda rack_id, pcs_id: deepcopy(healthy)).runtime_monitor_sample("pair_1")
live["refresh"] = {"source": "live_hardware"}

def one_live_confirmation(pair_id, **kwargs):
    calls["count"] += 1
    assert kwargs["fresh"] is True
    assert kwargs["wait_for_refresh"] is False
    return deepcopy(live)

service.status = one_live_confirmation
fallback = service._runtime_monitor_status("pair_1", service._pair("pair_1"))
assert calls["count"] == 1
assert fallback["refresh"]["source"] == "live_hardware_fallback_for_cache_quality"
assert fallback["refresh"]["cache_errors"]

print("Stage 3C synthetic cache and live-fallback checks: OK")
