from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from app.core.config import GatewayConfig, PcsDeviceConfig
from app.services.bms_pcs_control import BmsPcsControlService, ControlStatusBusyError


class DummyDriver:
    pass


class DummyStore:
    pass


def make_service() -> BmsPcsControlService:
    config = GatewayConfig()
    config.mode = "control_enabled"
    config.bms.write_enabled = True
    config.pcs.enabled = True
    config.pcs.write_enabled = True
    config.pcs.transport = "rtu"
    config.pcs.devices = [
        PcsDeviceConfig(asset_id=f"pcs_{i}", unit_id=i, enabled=True)
        for i in range(1, 5)
    ]
    for pair in config.control_sequence.pairs:
        pair.enabled = True
    return BmsPcsControlService(config, DummyDriver(), DummyDriver(), DummyStore())


def live_result(pair_id: str) -> dict[str, Any]:
    return {
        "pair": {"pair_id": pair_id},
        "timestamp": "2026-07-28T00:00:00+00:00",
        "runtime": {},
        "summary": {"pcs_operating_state": 1},
        "points": {},
        "errors": [],
        "workflow": {},
    }


def test_cached_status_is_default_and_does_not_read_hardware() -> None:
    service = make_service()
    called = 0

    def forbidden(pair_id: str, pair: Any) -> dict[str, Any]:
        nonlocal called
        called += 1
        raise AssertionError("live read must not be called")

    service._read_live_status = forbidden  # type: ignore[method-assign]
    result = service.status("pair_1")
    assert result["pair_id"] == "pair_1"
    assert result["run_status"] == "idle"
    assert called == 0


def test_duplicate_live_refresh_returns_cached_without_second_hardware_read() -> None:
    service = make_service()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def slow_read(pair_id: str, pair: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return live_result(pair_id)
        entered.set()
        assert release.wait(2)
        return live_result(pair_id)

    service._read_live_status = slow_read  # type: ignore[method-assign]

    seeded = service.status("pair_1", fresh=True)
    assert seeded["refresh"]["source"] == "live_hardware"

    result_holder: dict[str, Any] = {}

    def worker() -> None:
        result_holder["result"] = service.status("pair_1", fresh=True)

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(1)

    duplicate = service.status(
        "pair_1",
        fresh=True,
        wait_for_refresh=False,
        return_cached_if_busy=True,
    )
    assert duplicate["refresh"]["source"] == "cached_while_live_refresh_busy"
    assert duplicate["refresh"]["busy"] is True
    assert calls == 2

    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert result_holder["result"]["refresh"]["source"] == "live_hardware"
    assert calls == 2


def test_busy_without_cache_raises_conflict_exception() -> None:
    service = make_service()
    entered = threading.Event()
    release = threading.Event()

    def slow_read(pair_id: str, pair: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(2)
        return live_result(pair_id)

    service._read_live_status = slow_read  # type: ignore[method-assign]

    thread = threading.Thread(
        target=lambda: service.status("pair_1", fresh=True),
    )
    thread.start()
    assert entered.wait(1)

    with pytest.raises(ControlStatusBusyError):
        service.status(
            "pair_1",
            fresh=True,
            wait_for_refresh=False,
            return_cached_if_busy=True,
        )

    release.set()
    thread.join(2)
    assert not thread.is_alive()


def test_global_gate_prevents_parallel_hardware_refreshes_across_pairs() -> None:
    service = make_service()
    entered = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    guard = threading.Lock()

    def slow_read(pair_id: str, pair: Any) -> dict[str, Any]:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        if pair_id == "pair_1":
            entered.set()
            assert release.wait(2)
        with guard:
            active -= 1
        return live_result(pair_id)

    service._read_live_status = slow_read  # type: ignore[method-assign]

    first = threading.Thread(target=lambda: service.status("pair_1", fresh=True))
    first.start()
    assert entered.wait(1)

    with pytest.raises(ControlStatusBusyError):
        service.status(
            "pair_2",
            fresh=True,
            wait_for_refresh=False,
            return_cached_if_busy=False,
        )

    release.set()
    first.join(2)
    assert not first.is_alive()
    assert max_active == 1
