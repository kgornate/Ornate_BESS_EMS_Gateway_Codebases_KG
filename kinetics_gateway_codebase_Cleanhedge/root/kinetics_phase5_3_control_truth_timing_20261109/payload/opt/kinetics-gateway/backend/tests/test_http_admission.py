import asyncio

from app.services.http_admission import (
    HttpAdmissionController,
    is_priority_http_path,
    is_cache_read_http_path,
    is_safety_http_path,
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


def test_priority_path_classification_keeps_control_and_health_reachable():
    assert is_priority_http_path("/api/health")
    assert is_priority_http_path("/api/auth/login")
    assert is_priority_http_path("/api/diagnostics/runtime")
    assert is_priority_http_path("/api/control/bms_bank/reset")
    assert is_priority_http_path("/api/control-sequence/pair_1/status")
    assert not is_priority_http_path("/api/telemetry/snapshot")
    assert not is_priority_http_path("/api/events")


def test_safety_lane_remains_available_when_normal_capacity_is_full():
    async def scenario():
        controller = HttpAdmissionController(
            max_concurrent=2,
            reserved_priority=1,
            wait_timeout_seconds=0.01,
        )
        occupied = [
            await controller.acquire(priority=True),
            await controller.acquire(priority=True),
        ]
        assert all(lease is not None for lease in occupied)
        assert await controller.acquire(priority=True) is None

        safety = await controller.acquire(priority=True, safety=True)
        assert safety is not None
        controller.release(safety, safety=True)
        for lease in occupied:
            controller.release(lease)

    asyncio.run(scenario())


def test_only_mutating_safety_commands_use_emergency_lane():
    assert is_safety_http_path(
        "/api/control-sequence/pair_1/safe-stop", "POST"
    )
    assert is_safety_http_path(
        "/api/control-sequence/pair_1/zero-power", "POST"
    )
    assert is_safety_http_path(
        "/api/control-sequence/pair_1/abort", "POST"
    )
    assert not is_safety_http_path(
        "/api/control-sequence/pair_1/status", "GET"
    )


def test_cache_reads_have_a_lane_independent_of_slow_http_work():
    async def scenario():
        controller = HttpAdmissionController(
            max_concurrent=2,
            reserved_priority=1,
            wait_timeout_seconds=0.01,
        )
        occupied = [
            await controller.acquire(priority=True),
            await controller.acquire(priority=True),
        ]
        cache = await controller.acquire(priority=True, cache_read=True)
        assert cache is not None
        controller.release(cache)
        for lease in occupied:
            controller.release(lease)

    asyncio.run(scenario())

    assert is_cache_read_http_path(
        "/api/control-sequence/pair_1/status", "GET"
    )
    assert is_cache_read_http_path(
        "/api/control-sequence/status/all/compact", "GET"
    )
    assert not is_cache_read_http_path(
        "/api/control-sequence/pair_1/safe-stop", "POST"
    )
