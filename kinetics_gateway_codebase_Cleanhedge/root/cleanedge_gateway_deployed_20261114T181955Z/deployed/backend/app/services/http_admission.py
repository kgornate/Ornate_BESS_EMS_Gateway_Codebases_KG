from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

HttpLane = Literal["safety", "scheduler", "general"]


@dataclass(frozen=True)
class HttpAdmissionLease:
    lane: HttpLane

    @property
    def priority(self) -> bool:
        return self.lane != "general"


class HttpAdmissionController:
    """Bound HTTP work while reserving capacity for control and health traffic."""

    def __init__(
        self,
        *,
        max_concurrent: int,
        reserved_priority: int,
        wait_timeout_seconds: float,
    ) -> None:
        if max_concurrent < 3:
            raise ValueError("max_concurrent must be at least 3")
        if reserved_priority < 1 or reserved_priority >= max_concurrent:
            raise ValueError("reserved_priority must be between 1 and max_concurrent - 1")
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        self.max_concurrent = int(max_concurrent)
        self.reserved_priority = int(reserved_priority)
        self.wait_timeout_seconds = float(wait_timeout_seconds)
        safety_capacity = max(1, self.reserved_priority)
        scheduler_capacity = 1
        general_capacity = self.max_concurrent - safety_capacity - scheduler_capacity
        if general_capacity < 1:
            raise ValueError("HTTP lanes must leave at least one general slot")
        self.general_capacity = general_capacity
        self.capacities: dict[HttpLane, int] = {
            "safety": safety_capacity,
            "scheduler": scheduler_capacity,
            "general": general_capacity,
        }
        self._lanes = {
            lane: asyncio.BoundedSemaphore(capacity)
            for lane, capacity in self.capacities.items()
        }

    async def _acquire(self, semaphore: asyncio.BoundedSemaphore) -> None:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=self.wait_timeout_seconds,
        )

    async def acquire(
        self,
        *,
        lane: HttpLane | None = None,
        priority: bool | None = None,
    ) -> HttpAdmissionLease | None:
        selected: HttpLane = lane or ("safety" if priority else "general")
        try:
            await self._acquire(self._lanes[selected])
            return HttpAdmissionLease(lane=selected)
        except asyncio.TimeoutError:
            return None

    def release(self, lease: HttpAdmissionLease) -> None:
        self._lanes[lease.lane].release()


def http_request_lane(method: str, path: str) -> HttpLane:
    method = method.upper()
    # Keep this lane exclusive to the local automation supervisor. Sharing
    # its single slot with login/diagnostics traffic previously allowed a
    # Flutter reconnect to reject the cache-only snapshot.
    if path == "/api/internal/control-sequence/automation-snapshot":
        return "scheduler"
    if method != "GET" and (
        path.startswith("/api/control/")
        or path.startswith("/api/control-sequence/")
    ):
        return "safety"
    return "general"


def is_priority_http_request(method: str, path: str) -> bool:
    """Reserve capacity for liveness and writes, never routine dashboard reads.

    Flutter polls ``/api/control-sequence/...`` status routes.  Treating the
    complete prefix as priority allowed an unhealthy client to consume the
    capacity reserved for safe-stop and health traffic.  Control mutations are
    POST requests, while ordinary GET status traffic belongs in the bounded
    general lane.
    """
    return http_request_lane(method, path) != "general"


def is_priority_http_path(path: str) -> bool:
    """Compatibility helper retained for callers/tests from older phases."""
    return is_priority_http_request("GET", path)
