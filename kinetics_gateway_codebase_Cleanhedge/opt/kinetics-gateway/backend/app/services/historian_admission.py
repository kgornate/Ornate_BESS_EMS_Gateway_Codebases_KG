from __future__ import annotations

from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from typing import Iterator


class HistorianBusyError(RuntimeError):
    """Raised when bounded historian capacity is unavailable."""


class HistorianAdmissionController:
    def __init__(self, *, max_concurrent: int, wait_timeout_seconds: float) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.wait_timeout_seconds = max(0.001, float(wait_timeout_seconds))
        self._semaphore = BoundedSemaphore(self.max_concurrent)
        self._lock = Lock()
        self._active = 0
        self._peak_active = 0
        self._accepted = 0
        self._rejected = 0

    @contextmanager
    def lease(self) -> Iterator[None]:
        acquired = self._semaphore.acquire(timeout=self.wait_timeout_seconds)
        if not acquired:
            with self._lock:
                self._rejected += 1
            raise HistorianBusyError("Historian is busy; retry after the current query completes")
        with self._lock:
            self._active += 1
            self._accepted += 1
            self._peak_active = max(self._peak_active, self._active)
        try:
            yield
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)
            self._semaphore.release()

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "max_concurrent": self.max_concurrent,
                "wait_timeout_seconds": self.wait_timeout_seconds,
                "active": self._active,
                "peak_active": self._peak_active,
                "accepted": self._accepted,
                "rejected": self._rejected,
            }
