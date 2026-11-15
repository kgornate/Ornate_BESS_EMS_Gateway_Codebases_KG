from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from pathlib import Path

from app.core.config import GatewayConfig
from app.services.bms_pcs_control import BmsPcsControlService

# Synthetic healthy cache snapshot used by the runtime-monitor checks.
from datetime import datetime, timedelta, timezone

def point(value, *, raw=None, bitfields=None):
    item = {"value": value, "raw": value if raw is None else raw, "quality": "good"}
    if bitfields is not None:
        item["bitfields"] = bitfields
    return item

def healthy_snapshot(*, stale=False, setpoint=2.0):
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


class RecordingDriver:
    def __init__(self):
        self.writes = []

    def write_point(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        return {'ok': True, 'args': args}


def make_service(provider):
    config_path = Path(os.environ['KINETICS_CONFIG'])
    cfg = GatewayConfig.model_validate(json.loads(config_path.read_text()))
    return BmsPcsControlService(
        cfg,
        RecordingDriver(),
        RecordingDriver(),
        object(),
        snapshot_provider=provider,
    )


# 1) Safe-stop and abort must quiesce the monitor before physical stop.
service = make_service(lambda rack_id, pcs_id: deepcopy(healthy_snapshot()))
service._require_write_gates = lambda confirmation: None
order = []
service._quiesce_runtime_monitor = lambda pair_id, **kwargs: order.append('quiesce') or {'stopped': True}
service._safe_stop_internal = lambda *args, **kwargs: order.append('physical_stop') or {'errors': [], 'ok': True}
service.safe_stop('tester', 'pair_1', 'ok', open_bms=True)
assert order == ['quiesce', 'physical_stop'], order

order.clear()
service.abort('tester', 'pair_1', 'ok', open_bms=True)
assert order == ['quiesce', 'physical_stop'], order


# 2) A stop requested while a cache sample is in flight must exit before
#    evaluating intentionally opened contactors/zero BMS limits.
started = threading.Event()

bad = healthy_snapshot()
bad['rack']['telemetry']['pre_charge_state']['value'] = 0
bad['rack']['telemetry']['contactor_state']['value'] = 0
bad['rack']['telemetry']['contactor_state']['bitfields'] = {
    'positive_contactor_state': 0,
    'negitive_contactor_state': 0,
}
bad['rack']['telemetry']['irack_dsg_limit']['value'] = 0.0


def delayed_provider(rack_id, pcs_id):
    started.set()
    time.sleep(0.25)
    return deepcopy(bad)

service = make_service(delayed_provider)
service._update_runtime(
    'pair_1',
    commanded_power_kw=2.0,
    requested_power_kw=2.0,
    requested_direction='discharge',
)
service._ensure_runtime_monitor('tester', service._pair('pair_1'))
assert started.wait(2.0), 'monitor did not enter snapshot provider'
result = service._quiesce_runtime_monitor(
    'pair_1',
    reason='unit_test_operator_safe_stop',
    timeout_seconds=2.0,
)
assert result['stopped'] is True, result
stats = service.runtime_monitor_diagnostics()['pairs']['pair_1']
assert stats['safety_stops'] == 0, stats
assert service.bms_driver.writes == [], service.bms_driver.writes
assert service.pcs_driver.writes == [], service.pcs_driver.writes


# 3) Per-pair safe-stop serialization lock must exist.
assert 'pair_1' in service._safe_stop_locks
assert 'pair_1' in service._monitor_stop_events

print('Stage 3D safe-stop race synthetic checks: OK')
