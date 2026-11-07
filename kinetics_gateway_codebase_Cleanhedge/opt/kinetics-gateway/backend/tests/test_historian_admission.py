from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.services.historian_admission import HistorianAdmissionController, HistorianBusyError


def test_historian_admission_rejects_parallel_expensive_work():
    controller = HistorianAdmissionController(max_concurrent=1, wait_timeout_seconds=0.01)
    entered = Event()
    release = Event()

    def hold_lease():
        with controller.lease():
            entered.set()
            assert release.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(hold_lease)
        assert entered.wait(timeout=1)
        with pytest.raises(HistorianBusyError):
            with controller.lease():
                pass
        release.set()
        future.result(timeout=2)

    status = controller.snapshot()
    assert status["peak_active"] == 1
    assert status["accepted"] == 1
    assert status["rejected"] == 1
    assert status["active"] == 0
