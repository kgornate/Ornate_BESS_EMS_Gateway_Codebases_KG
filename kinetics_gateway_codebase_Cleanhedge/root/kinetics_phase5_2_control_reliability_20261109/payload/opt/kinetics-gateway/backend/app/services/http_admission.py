from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class HttpAdmissionLease:
    priority: bool


class HttpAdmissionController:
    """Bound HTTP work while keeping one lane exclusive to safety commands."""

    def __init__(
        self,
        *,
        max_concurrent: int,
        reserved_priority: int,
        wait_timeout_seconds: float,
    ) -> None:
        if max_concurrent < 2:
            raise ValueError("max_concurrent must be at least 2")
        if reserved_priority < 1 or reserved_priority >= max_concurrent:
            raise ValueError("reserved_priority must be between 1 and max_concurrent - 1")
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        self.max_concurrent = int(max_concurrent)
        self.reserved_priority = int(reserved_priority)
        self.general_capacity = self.max_concurrent - self.reserved_priority
        self.wait_timeout_seconds = float(wait_timeout_seconds)
        # Normal traffic keeps its existing configured capacity. Safety has
        # one additional emergency-only slot and therefore cannot be crowded
        # out by already-admitted status requests.
        self._non_safety = asyncio.BoundedSemaphore(self.max_concurrent)
        self._safety = asyncio.BoundedSemaphore(1)
        self._general = asyncio.BoundedSemaphore(self.general_capacity)

    async def _acquire(self, semaphore: asyncio.BoundedSemaphore) -> None:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=self.wait_timeout_seconds,
        )

    async def acquire(
        self, *, priority: bool, safety: bool = False
    ) -> HttpAdmissionLease | None:
        general_acquired = False
        try:
            if safety:
                await self._acquire(self._safety)
                return HttpAdmissionLease(priority=True)
            if not priority:
                await self._acquire(self._general)
                general_acquired = True
            await self._acquire(self._non_safety)
            return HttpAdmissionLease(priority=priority)
        except asyncio.TimeoutError:
            if general_acquired:
                self._general.release()
            return None

    def release(self, lease: HttpAdmissionLease, *, safety: bool = False) -> None:
        if safety:
            self._safety.release()
            return
        self._non_safety.release()
        if not lease.priority:
            self._general.release()


def is_priority_http_path(path: str) -> bool:
    """Keep safety/control and liveness paths reachable during dashboard overload."""
    return (
        path == "/api/health"
        or path == "/api/auth/login"
        or path == "/api/diagnostics/runtime"
        or path.startswith("/api/control/")
        or path.startswith("/api/control-sequence/")
    )


def is_safety_http_path(path: str, method: str) -> bool:
    """Identify commands that must remain reachable during status overload."""
    if method.upper() != "POST":
        return False
    return (
        path == "/api/control-sequence/safe-stop-all"
        or path.endswith("/safe-stop")
        or path.endswith("/abort")
        or path.endswith("/zero-power")
    )
