import asyncio

from app.services.http_admission import (
    HttpAdmissionController,
    is_priority_http_path,
    is_priority_http_request,
)


def test_general_capacity_is_bounded_and_priority_reserve_remains_available():
    async def scenario():
        controller = HttpAdmissionController(
            max_concurrent=3,
            reserved_priority=1,
            wait_timeout_seconds=0.01,
        )
        general_one = await controller.acquire(priority=False)
        general_two = await controller.acquire(priority=False)
        assert general_one is not None
        assert general_two is not None
        assert await controller.acquire(priority=False) is None

        priority = await controller.acquire(priority=True)
        assert priority is not None
        assert await controller.acquire(priority=True) is None

        controller.release(priority)
        controller.release(general_two)
        controller.release(general_one)

    asyncio.run(scenario())


def test_priority_request_classification_reserves_capacity_for_writes_and_health():
    assert is_priority_http_path("/api/health")
    assert is_priority_http_path("/api/auth/login")
    assert is_priority_http_path("/api/diagnostics/runtime")
    assert is_priority_http_request("POST", "/api/control/bms_bank/reset")
    assert is_priority_http_request("POST", "/api/control-sequence/pair_1/safe-stop")
    assert is_priority_http_request("POST", "/api/control-sequence/safe-stop-all")
    assert not is_priority_http_request("GET", "/api/control-sequence/pair_1/status")
    assert not is_priority_http_request("GET", "/api/control-sequence/status/all/compact")
    assert not is_priority_http_path("/api/control-sequence/pair_1/status")
    assert not is_priority_http_path("/api/telemetry/snapshot")
    assert not is_priority_http_path("/api/events")
