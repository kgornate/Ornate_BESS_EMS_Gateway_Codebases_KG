from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


SCHEDULER = Path(os.getenv("KINETICS_TEST_SCHEDULER", "/root/bess_solar_time_automation.py"))


def load_scheduler():
    spec = importlib.util.spec_from_file_location("field_recovery_scheduler", SCHEDULER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_1024(*, hard_fault: bool = False) -> dict:
    module_fault = "9" if hard_fault else "None"
    message = (
        "PCS entered fault shutdown during startup: state=1024, "
        f"module_fault_word_10={module_fault}, latched_fault_word_7=None; "
        "PCS zero-setpoint/stop was requested automatically"
    )
    return {
        "run_status": "failed",
        "last_error": message,
        "steps": [{"key": "pcs_start", "status": "failed", "message": message}],
    }


def tracking_item(actual: float, setpoint: float = -5.0) -> dict:
    return {
        "summary": {
            "pcs_power_setpoint_kw": setpoint,
            "pcs_actual_power_kw": actual,
            "pcs_operating_state": 80,
            "precharge_state": 3,
        },
        "runtime": {"run_status": "success", "stage": "automatic_sequence_complete"},
        "errors": [],
    }


def test_only_narrow_state_1024_failure_is_recoverable() -> None:
    scheduler = load_scheduler()
    assert scheduler.recoverable_pcs_1024(runtime_1024())
    assert not scheduler.recoverable_pcs_1024(runtime_1024(hard_fault=True))
    assert not scheduler.recoverable_pcs_1024({"run_status": "success", "steps": []})


def test_tracking_evidence_preserves_trigger_values() -> None:
    scheduler = load_scheduler()
    evidence = scheduler.target_tracking_evidence(tracking_item(-0.2), "charge", 5.0)
    assert evidence["tracking_ok"] is False
    assert evidence["pcs_actual_power_kw"] == -0.2
    assert evidence["pcs_power_setpoint_kw"] == -5.0
    assert evidence["runtime_stage"] == "automatic_sequence_complete"


def test_solar_evidence_distinguishes_stale_from_low(monkeypatch) -> None:
    scheduler = load_scheduler()
    stale = {
        "solar": {
            "online": True,
            "timestamp": "2026-11-12T08:00:00+05:30",
            "active_power_kw": {"v": 1200.0, "q": "good"},
            "errors": ["Modbus exception 11 for FC03"],
        }
    }
    evidence = scheduler.solar_evidence(stale, 15, 80.0)
    assert evidence["condition"] == "stale"
    assert evidence["value_kw"] == 1200.0
    assert evidence["errors"] == ["Modbus exception 11 for FC03"]


def test_single_fresh_readback_mismatch_does_not_stop_all(monkeypatch) -> None:
    scheduler = load_scheduler()
    monkeypatch.setattr(scheduler, "ACTIVE_READBACK_CONFIRMATIONS", 3)
    gateway = SimpleNamespace(
        request=lambda *args, **kwargs: tracking_item(-0.1)
    )
    supervisor = scheduler.Supervisor(gateway)
    supervisor.active_pairs = {"pair_1"}
    supervisor.active_since = None
    supervisor.all_status = lambda: {"by_pair_id": {"pair_1": tracking_item(-0.1)}}
    stopped: list[str] = []
    supervisor.stop_all = stopped.append
    supervisor.event = lambda *args, **kwargs: None
    config = {"pairs": {"pair_1": {"enabled": True, "charge_kw": 5.0}}}

    supervisor.reconcile(config, "charge")

    assert stopped == []
    assert supervisor.active_readback_failures["pair_1"] == 1


def test_three_fresh_readback_mismatches_trigger_safe_stop(monkeypatch) -> None:
    scheduler = load_scheduler()
    monkeypatch.setattr(scheduler, "ACTIVE_READBACK_CONFIRMATIONS", 3)
    gateway = SimpleNamespace(request=lambda *args, **kwargs: tracking_item(-0.1))
    supervisor = scheduler.Supervisor(gateway)
    supervisor.active_pairs = {"pair_1"}
    supervisor.active_since = None
    supervisor.all_status = lambda: {"by_pair_id": {"pair_1": tracking_item(-0.1)}}
    stopped: list[str] = []
    supervisor.stop_all = stopped.append
    supervisor.event = lambda *args, **kwargs: None
    config = {"pairs": {"pair_1": {"enabled": True, "charge_kw": 5.0}}}

    for _ in range(3):
        supervisor.reconcile(config, "charge")

    assert len(stopped) == 1
    assert "Confirmed active readback loss for pair_1" in stopped[0]


def test_good_confirmation_clears_cached_mismatch(monkeypatch) -> None:
    scheduler = load_scheduler()
    gateway = SimpleNamespace(request=lambda *args, **kwargs: tracking_item(-5.0))
    supervisor = scheduler.Supervisor(gateway)
    supervisor.active_pairs = {"pair_1"}
    supervisor.active_since = None
    supervisor.all_status = lambda: {"by_pair_id": {"pair_1": tracking_item(-0.1)}}
    supervisor.stop_all = lambda reason: (_ for _ in ()).throw(AssertionError(reason))
    supervisor.event = lambda *args, **kwargs: None
    config = {"pairs": {"pair_1": {"enabled": True, "charge_kw": 5.0}}}

    supervisor.reconcile(config, "charge")

    assert supervisor.active_readback_failures["pair_1"] == 0


def test_operating_window_guard_blocks_late_restart(monkeypatch) -> None:
    scheduler = load_scheduler()
    monkeypatch.setattr(scheduler, "MIN_RESTART_REMAINING_SECONDS", 480.0)
    supervisor = scheduler.Supervisor(SimpleNamespace())
    supervisor.publish = lambda message, **extra: setattr(
        supervisor, "published", (message, extra)
    )
    supervisor.prepare_pairs = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("late preparation must be inhibited")
    )
    now = datetime.now(scheduler.IST)
    plan = {
        "direction": "charge",
        "mode": "operate",
        "active_by": now - timedelta(hours=1),
        "stop_at": now + timedelta(seconds=120),
    }
    monkeypatch.setattr(scheduler, "schedule_plan", lambda config, current: plan)
    monkeypatch.setattr(scheduler, "solar_sample", lambda view, age: (500.0, True, 0.1))

    supervisor.cycle(
        {"enabled": True, "solar": {"max_age_seconds": 15}}, {}, 0.0
    )

    assert supervisor.phase == "waiting_window_guard"
    assert "insufficient operating window" in supervisor.published[0]


def test_recovery_attempt_is_bounded_per_pair_and_window() -> None:
    scheduler = load_scheduler()
    supervisor = scheduler.Supervisor(SimpleNamespace())
    key = ("2026-11-12T15:00:00+05:30", "pair_4")
    supervisor.recovery_attempts.add(key)
    try:
        supervisor._recover_pcs_1024(
            "pair_4", "discharge", key[0], runtime_1024()
        )
    except RuntimeError as error:
        assert "already attempted" in str(error)
    else:
        raise AssertionError("second recovery attempt was not rejected")


def test_recovery_executes_zero_prepare_then_pair_safe_stop(monkeypatch, tmp_path) -> None:
    scheduler = load_scheduler()
    monkeypatch.setattr(scheduler, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(scheduler, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(scheduler, "RECOVERY_TIMEOUT_SECONDS", 2.0)

    calls: list[tuple[str, str, object]] = []

    class Gateway:
        compact_reads = 0

        def request(self, method, path, body=None, timeout=15, discard=False):
            calls.append((method, path, body))
            if method == "POST" and path.endswith("/automatic-start"):
                assert body["power_kw"] == 0.0
                assert body["prepare_only"] is True
                return {"run_id": "recovery-run"}
            if method == "POST" and path.endswith("/safe-stop"):
                assert body["open_bms"] is True
                return {"ok": True}
            if method == "GET":
                self.compact_reads += 1
                if self.compact_reads == 1:
                    return {
                        "runtime": {
                            "run_id": "recovery-run",
                            "run_status": "prepared",
                            "stage": "automatic_prepared_zero_power",
                            "requested_direction": "discharge",
                            "requested_power_kw": 0.0,
                            "last_error": None,
                        }
                    }
                return {
                    "summary": {
                        "precharge_state": 0,
                        "positive_contactor_closed": False,
                        "negative_contactor_closed": False,
                        "pcs_operating_state": 1,
                        "pcs_power_setpoint_kw": 0.0,
                        "pcs_actual_power_kw": 0.0,
                    }
                }
            raise AssertionError((method, path, body))

    supervisor = scheduler.Supervisor(Gateway())
    supervisor._recover_pcs_1024(
        "pair_4",
        "discharge",
        "2026-11-12T15:00:00+05:30",
        runtime_1024(),
    )

    assert [call[0:2] for call in calls] == [
        ("POST", "/api/control-sequence/pair_4/automatic-start"),
        ("GET", "/api/control-sequence/pair_4/status/compact"),
        ("POST", "/api/control-sequence/pair_4/safe-stop"),
        ("GET", "/api/control-sequence/pair_4/status/compact"),
    ]
