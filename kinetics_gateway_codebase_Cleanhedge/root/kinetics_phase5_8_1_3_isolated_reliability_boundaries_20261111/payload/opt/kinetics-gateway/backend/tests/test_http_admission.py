import asyncio

from app.services.http_admission import (
    HttpAdmissionController,
    http_request_lane,
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
        assert general_two is None

        priority = await controller.acquire(priority=True)
        assert priority is not None
        assert await controller.acquire(priority=True) is None

        controller.release(priority)
        controller.release(general_one)

    asyncio.run(scenario())


def test_priority_request_classification_reserves_capacity_for_writes_and_health():
    assert is_priority_http_path("/api/health")
    assert is_priority_http_path("/api/auth/login")
    assert is_priority_http_path("/api/diagnostics/runtime")
    assert is_priority_http_request("POST", "/api/control/bms_bank/reset")
    assert is_priority_http_request("POST", "/api/control-sequence/pair_1/safe-stop")
    assert is_priority_http_request("POST", "/api/control-sequence/safe-stop-all")
    assert is_priority_http_request(
        "GET", "/api/internal/control-sequence/automation-snapshot"
    )
    assert not is_priority_http_request("GET", "/api/control-sequence/pair_1/status")
    assert not is_priority_http_request("GET", "/api/control-sequence/status/all/compact")
    assert not is_priority_http_path("/api/control-sequence/pair_1/status")
    assert not is_priority_http_path("/api/telemetry/snapshot")
    assert not is_priority_http_path("/api/events")


def test_safety_lane_is_independent_from_stuck_scheduler_and_general_lanes():
    async def scenario():
        controller = HttpAdmissionController(
            max_concurrent=4,
            reserved_priority=1,
            wait_timeout_seconds=0.01,
        )
        scheduler = await controller.acquire(lane="scheduler")
        general_one = await controller.acquire(lane="general")
        general_two = await controller.acquire(lane="general")
        assert scheduler and general_one and general_two
        assert await controller.acquire(lane="scheduler") is None
        assert await controller.acquire(lane="general") is None
        safety = await controller.acquire(lane="safety")
        assert safety is not None
        controller.release(safety)
        controller.release(general_two)
        controller.release(general_one)
        controller.release(scheduler)

    asyncio.run(scenario())


def test_scheduler_reads_never_share_the_physical_safety_lane():
    assert http_request_lane(
        "GET", "/api/internal/control-sequence/automation-snapshot"
    ) == "scheduler"
    assert http_request_lane(
        "POST", "/api/control-sequence/pair_1/abort"
    ) == "safety"
    assert http_request_lane("GET", "/api/control-sequence/pair_1/status") == "general"
