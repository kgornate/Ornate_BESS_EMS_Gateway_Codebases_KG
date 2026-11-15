from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from app.services.bms_pcs_control import BmsPcsControlService
from app.services.gateway_service import GatewayService


def test_runtime_snapshot_drops_recursive_step_results() -> None:
    service = BmsPcsControlService.__new__(BmsPcsControlService)
    service._lock = threading.RLock()
    service._runtime = {
        "pair_4": {
            "pair_id": "pair_4",
            "stage": "automatic:power_precheck",
            "steps": [
                {
                    "key": "pcs_start",
                    "label": "Start PCS",
                    "status": "success",
                    "started_at": "2026-11-11T00:00:00+00:00",
                    "completed_at": "2026-11-11T00:00:01+00:00",
                    "result": {
                        "runtime": {"steps": [{"result": list(range(10000))}]},
                        "points": {"raw": list(range(10000))},
                    },
                }
            ],
        }
    }

    started = time.monotonic()
    snapshot = service._runtime_snapshot("pair_4")
    assert time.monotonic() - started < 0.1
    assert snapshot["steps"] == [
        {
            "key": "pcs_start",
            "label": "Start PCS",
            "status": "success",
            "started_at": "2026-11-11T00:00:00+00:00",
            "completed_at": "2026-11-11T00:00:01+00:00",
            "elapsed_ms": 1000,
        }
    ]


def test_control_pair_snapshot_does_not_wait_for_publish_lock() -> None:
    service = GatewayService.__new__(GatewayService)
    service._control_cache_lock = threading.Lock()
    service._control_pair_cache = {
        "pair_4": {"sequence": 9, "timestamp": "now", "bank": {}, "rack": {}, "pcs": {}}
    }
    service.config = SimpleNamespace(
        control_sequence=SimpleNamespace(
            pairs=[SimpleNamespace(pair_id="pair_4", rack_id=4, pcs_asset_id="pcs_4")]
        )
    )

    service._control_cache_lock.acquire()
    try:
        started = time.monotonic()
        snapshot = service.control_pair_snapshot(4, "pcs_4")
        elapsed = time.monotonic() - started
    finally:
        service._control_cache_lock.release()

    assert elapsed < 0.1
    assert snapshot["sequence"] == 9


def test_health_bypasses_http_admission() -> None:
    source = Path(__file__).parents[1].joinpath("app/main.py").read_text()
    assert 'bypass_admission = request.url.path == "/api/health"' in source
    assert "lease = None if bypass_admission else await http_admission.acquire" in source


def test_step_writer_never_retains_result_payload() -> None:
    source = Path(__file__).parents[1].joinpath(
        "app/services/bms_pcs_control.py"
    ).read_text()
    assert 'step.pop("result", None)' in source
    assert 'step["result"] = result' not in source
