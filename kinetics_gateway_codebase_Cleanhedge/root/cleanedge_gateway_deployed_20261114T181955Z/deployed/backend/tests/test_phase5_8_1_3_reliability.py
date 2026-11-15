from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from app.services.gateway_service import GatewayService


class _ControlSnapshotStub:
    def all_pair_status_compact(self):
        pair = {
            "pair": {"pair_id": "pair_1", "rack_id": 1},
            "runtime": {"run_status": "running", "stage": "automatic:power_precheck"},
            "summary": {},
            "workflow": {},
        }
        return {
            "pairs": [pair],
            "summary": {},
            "safe_stop_all": {"status": "idle"},
        }


def test_scheduler_view_never_waits_for_main_gateway_lock():
    service = GatewayService.__new__(GatewayService)
    service._lock = threading.RLock()
    service._control_cache_lock = threading.Lock()
    service._scheduler_telemetry_cache = {
        "sequence": 7,
        "timestamp": "2026-11-11T00:00:00+00:00",
        "racks": {
            1: {"online": True, "timestamp": "now", "soc": {"v": 50, "q": "good"}}
        },
        "solar": {"online": True, "timestamp": "now", "active_power_kw": {"v": 500}},
    }
    service.control_sequence = _ControlSnapshotStub()

    locked = threading.Event()
    release = threading.Event()

    def hold_main_lock():
        with service._lock:
            locked.set()
            release.wait(2)

    holder = threading.Thread(target=hold_main_lock)
    holder.start()
    assert locked.wait(1)
    started = time.monotonic()
    view = service.scheduler_control_view()
    elapsed = time.monotonic() - started
    release.set()
    holder.join(1)
    assert elapsed < 0.1
    assert view["sequence"] == 7
    assert view["by_pair_id"]["pair_1"]["control_view"]["soc"]["v"] == 50


def test_scheduler_source_has_circuit_breaker_and_short_snapshot_timeout():
    source = Path("/root/bess_solar_time_automation.py").read_text()
    assert "snapshot_next_attempt" in source
    assert "snapshot_failures" in source
    assert "SNAPSHOT_REQUEST_TIMEOUT_SECONDS" in source
    assert "random.uniform(0, 0.5)" in source


def test_each_automatic_stage_publishes_real_runtime_stage():
    source = Path(__file__).parents[1].joinpath(
        "app/services/bms_pcs_control.py"
    ).read_text()
    for stage in (
        "pcs_configuration",
        "bms_precharge",
        "pcs_start",
        "power_precheck",
        "power_ramp",
        "power_tracking",
    ):
        assert f'stage="automatic:{stage}"' in source
    assert 'stage=f"verification:{stage}:{alias}"' in source
    assert "abort_event.is_set()" in source
