from __future__ import annotations

import threading
import time

import pytest

from app.protocols.io_arbiter import IoArbiterTimeout, IoPriority, PriorityIoArbiter


def _wait_for_queue(arbiter: PriorityIoArbiter, count: int) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if arbiter.snapshot()["queued_total"] == count:
            return
        time.sleep(0.005)
    raise AssertionError(f"arbiter queue did not reach {count}: {arbiter.snapshot()}")


def test_safety_overtakes_queued_telemetry_after_current_transaction() -> None:
    arbiter = PriorityIoArbiter("test", aging_seconds=10.0)
    release = threading.Event()
    order: list[str] = []

    def active_transaction() -> None:
        with arbiter.slot(IoPriority.TELEMETRY):
            order.append("active")
            release.wait(2.0)

    def request(name: str, priority: IoPriority) -> None:
        with arbiter.slot(priority):
            order.append(name)

    active = threading.Thread(target=active_transaction)
    active.start()
    deadline = time.monotonic() + 2.0
    while not arbiter.snapshot()["active"] and time.monotonic() < deadline:
        time.sleep(0.005)
    telemetry = threading.Thread(target=request, args=("telemetry", IoPriority.TELEMETRY))
    telemetry.start()
    _wait_for_queue(arbiter, 1)
    safety = threading.Thread(target=request, args=("safety", IoPriority.SAFETY))
    safety.start()
    _wait_for_queue(arbiter, 2)
    release.set()
    for thread in (active, telemetry, safety):
        thread.join(2.0)

    assert order == ["active", "safety", "telemetry"]
    snapshot = arbiter.snapshot()
    assert snapshot["completed"] == 3
    assert snapshot["queued_total"] == 0
    assert snapshot["by_priority"]["safety"]["admitted"] == 1


def test_wait_timeout_is_bounded_and_observable() -> None:
    arbiter = PriorityIoArbiter("test-timeout")
    with arbiter.slot(IoPriority.TELEMETRY):
        with pytest.raises(IoArbiterTimeout):
            with arbiter.slot(IoPriority.CONTROL, timeout_seconds=0.01):
                pass
    snapshot = arbiter.snapshot()
    assert snapshot["timeouts"] == 1
    assert snapshot["by_priority"]["control"]["timeouts"] == 1


def test_equal_priority_is_fifo() -> None:
    arbiter = PriorityIoArbiter("test-fifo", aging_seconds=10.0)
    order: list[int] = []

    def queued(number: int) -> None:
        with arbiter.slot(IoPriority.VERIFICATION):
            order.append(number)

    with arbiter.slot(IoPriority.TELEMETRY):
        first = threading.Thread(target=queued, args=(1,))
        second = threading.Thread(target=queued, args=(2,))
        first.start()
        _wait_for_queue(arbiter, 1)
        second.start()
        _wait_for_queue(arbiter, 2)
    first.join(2.0)
    second.join(2.0)
    assert order == [1, 2]
