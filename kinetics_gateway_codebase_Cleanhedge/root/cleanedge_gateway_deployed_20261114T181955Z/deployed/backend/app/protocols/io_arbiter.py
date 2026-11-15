from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator


class IoPriority(IntEnum):
    """Lower numeric values receive the bus first."""

    SAFETY = 0
    CONTROL = 10
    VERIFICATION = 20
    TELEMETRY = 30
    HISTORIAN = 40


class IoArbiterTimeout(TimeoutError):
    pass


@dataclass
class _Waiter:
    ticket: int
    priority: IoPriority
    queued_at: float


class PriorityIoArbiter:
    """Synchronous, starvation-resistant admission for one physical I/O lane.

    A Modbus transaction already on the wire is never interrupted. When that
    transaction finishes, the highest-priority queued request is admitted.
    Aging promotes a request by one priority band for every ``aging_seconds``
    spent waiting, preventing telemetry from being starved indefinitely.
    """

    def __init__(self, name: str, *, aging_seconds: float = 2.0) -> None:
        self.name = name
        self.aging_seconds = max(float(aging_seconds), 0.001)
        self._condition = threading.Condition()
        self._active = False
        self._ticket = 0
        self._waiters: list[_Waiter] = []
        self._completed = 0
        self._timeouts = 0
        self._total_wait_seconds = 0.0
        self._max_wait_seconds = 0.0
        self._by_priority = {
            priority.name.lower(): {"admitted": 0, "timeouts": 0}
            for priority in IoPriority
        }

    def _effective_priority(self, waiter: _Waiter, now: float) -> int:
        bands = int((now - waiter.queued_at) / self.aging_seconds)
        return max(int(IoPriority.SAFETY), int(waiter.priority) - bands * 10)

    def _next_waiter(self, now: float) -> _Waiter | None:
        if not self._waiters:
            return None
        return min(
            self._waiters,
            key=lambda item: (self._effective_priority(item, now), item.ticket),
        )

    @contextmanager
    def slot(
        self,
        priority: IoPriority = IoPriority.TELEMETRY,
        *,
        timeout_seconds: float | None = 30.0,
    ) -> Iterator[None]:
        priority = IoPriority(priority)
        queued_at = time.monotonic()
        with self._condition:
            self._ticket += 1
            waiter = _Waiter(self._ticket, priority, queued_at)
            self._waiters.append(waiter)
            deadline = None if timeout_seconds is None else queued_at + max(timeout_seconds, 0.0)
            while self._active or self._next_waiter(time.monotonic()) is not waiter:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._waiters.remove(waiter)
                    self._timeouts += 1
                    self._by_priority[priority.name.lower()]["timeouts"] += 1
                    self._condition.notify_all()
                    raise IoArbiterTimeout(
                        f"Timed out waiting for {self.name} I/O lane at {priority.name.lower()} priority"
                    )
                self._condition.wait(
                    timeout=(
                        self.aging_seconds
                        if remaining is None
                        else min(remaining, self.aging_seconds)
                    )
                )
            self._waiters.remove(waiter)
            self._active = True
            waited = time.monotonic() - queued_at
            self._total_wait_seconds += waited
            self._max_wait_seconds = max(self._max_wait_seconds, waited)
            self._by_priority[priority.name.lower()]["admitted"] += 1
        try:
            yield
        finally:
            with self._condition:
                self._active = False
                self._completed += 1
                self._condition.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            queued = {priority.name.lower(): 0 for priority in IoPriority}
            for waiter in self._waiters:
                queued[waiter.priority.name.lower()] += 1
            admitted = sum(item["admitted"] for item in self._by_priority.values())
            return {
                "name": self.name,
                "active": self._active,
                "queued": queued,
                "queued_total": len(self._waiters),
                "completed": self._completed,
                "timeouts": self._timeouts,
                "average_wait_ms": round(
                    self._total_wait_seconds * 1000.0 / admitted, 3
                ) if admitted else 0.0,
                "max_wait_ms": round(self._max_wait_seconds * 1000.0, 3),
                "by_priority": {
                    name: dict(values) for name, values in self._by_priority.items()
                },
            }
