#!/usr/bin/env python3
"""Persistent solar-qualified scheduler for the Phase 5.7.2.2 control API."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
import random
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_USER = os.getenv("API_USER", "internal")
API_PASS = os.getenv("API_PASS", "Internal@123")
CONFIG_PATH = Path(os.getenv("KINETICS_AUTOMATION_CONFIG", "/mnt/ems-logs/kinetics-gateway/automation_schedule.json"))
STATUS_PATH = Path(os.getenv("KINETICS_AUTOMATION_STATUS", "/mnt/ems-logs/kinetics-gateway/automation_status.json"))
EVENTS_PATH = Path(os.getenv("KINETICS_AUTOMATION_EVENTS", "/mnt/ems-logs/kinetics-gateway/automation_events.jsonl"))
LOCK_PATH = Path(os.getenv("LOCK_FILE", "/run/bess-seven-day-v302.lock"))
PAIR_IDS = ("pair_1", "pair_2", "pair_3", "pair_4")
POLL_SECONDS = float(os.getenv("AUTOMATION_POLL_SECONDS", "2"))
ACTIVE_READBACK_CONFIRMATIONS = max(
    2, int(os.getenv("AUTOMATION_ACTIVE_READBACK_CONFIRMATIONS", "3"))
)
ACTIVE_READBACK_GRACE_SECONDS = max(
    0.0, float(os.getenv("AUTOMATION_ACTIVE_READBACK_GRACE_SECONDS", "45"))
)
MIN_RESTART_REMAINING_SECONDS = max(
    0.0, float(os.getenv("AUTOMATION_MIN_RESTART_REMAINING_SECONDS", "480"))
)
RECOVERY_TIMEOUT_SECONDS = max(
    60.0, float(os.getenv("AUTOMATION_PCS_1024_RECOVERY_TIMEOUT_SECONDS", "180"))
)
SNAPSHOT_LOSS_SAFE_STOP_SECONDS = max(
    10.0, float(os.getenv("AUTOMATION_SNAPSHOT_LOSS_SAFE_STOP_SECONDS", "30"))
)
SOLAR_TELEMETRY_LOSS_SAFE_STOP_SECONDS = max(
    60.0,
    float(os.getenv("AUTOMATION_SOLAR_TELEMETRY_LOSS_SAFE_STOP_SECONDS", "120")),
)
CONTROL_STATUS_RETRY_SECONDS = max(
    5.0, float(os.getenv("AUTOMATION_CONTROL_STATUS_RETRY_SECONDS", "15"))
)
STOP = False


def log(message: str) -> None:
    print(f"{datetime.now(IST).isoformat()} {message}", flush=True)


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


class Gateway:
    def __init__(self) -> None:
        self.token: str | None = None

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 15, discard: bool = False) -> dict[str, Any]:
        encoded = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(BASE_URL + path, data=encoded, method=method)
        request.add_header("Accept", "application/json")
        if encoded is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if discard:
                    return {"ok": True}
                raw = response.read(2 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            if error.code == 401 and path != "/api/auth/login":
                self.login()
                return self.request(method, path, body, timeout, discard)
            raise RuntimeError(f"HTTP {error.code} {path}: {error.read(4096).decode(errors='replace')}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"gateway unavailable for {path}: {error}") from error
        return json.loads(raw) if raw else {}

    def login(self) -> None:
        self.token = None
        self.token = str(self.request("POST", "/api/auth/login", {"username": API_USER, "password": API_PASS})["access_token"])

    def wait_ready(self) -> None:
        while not STOP:
            try:
                health = self.request("GET", "/api/health", timeout=10)
                if health.get("status") == "ok" and (health.get("startup") or {}).get("ready"):
                    self.login()
                    return
            except Exception as error:
                log(f"Waiting for gateway: {error}")
            time.sleep(5)


class SnapshotUnavailable(RuntimeError):
    """Retryable loss of the scheduler's local cache-only snapshot."""


def retryable_http_capacity_error(error: Exception) -> bool:
    message = str(error).lower()
    return "http 503" in message and (
        "retryable" in message or "capacity is busy" in message
    )


def desired_window(config: dict[str, Any], now: datetime) -> str | None:
    if not config.get("enabled") or now.isoweekday() not in set(config.get("days") or []):
        return None
    current = now.strftime("%H:%M")
    for direction in ("charge", "discharge"):
        window = config.get(direction) or {}
        if str(window.get("start")) <= current < str(window.get("stop")):
            return direction
    return None


def schedule_plan(config: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Return today's preparation/dispatch plan for the current instant."""
    if not config.get("enabled") or now.isoweekday() not in set(config.get("days") or []):
        return None
    timing = config.get("timing") or {}
    prepare_lead = int(timing.get("preparation_lead_minutes", 15))
    dispatch_lead = int(timing.get("power_dispatch_lead_seconds", 120))
    for direction in ("charge", "discharge"):
        window = config.get(direction) or {}
        start_clock = datetime.strptime(str(window.get("start")), "%H:%M").time()
        stop_clock = datetime.strptime(str(window.get("stop")), "%H:%M").time()
        start = datetime.combine(now.date(), start_clock, tzinfo=IST)
        stop = datetime.combine(now.date(), stop_clock, tzinfo=IST)
        prepare = start - timedelta(minutes=prepare_lead)
        dispatch = start - timedelta(seconds=dispatch_lead)
        if prepare <= now < stop:
            return {
                "direction": direction,
                "mode": "operate" if now >= dispatch else "prepare",
                "prepare_at": prepare,
                "dispatch_at": dispatch,
                "active_by": start,
                "stop_at": stop,
            }
    return None


def solar_sample(view: dict[str, Any], max_age: int) -> tuple[float | None, bool, float | None]:
    solar = view.get("solar") or {}
    point = solar.get("active_power_kw") or {}
    value = None
    if str(point.get("q", point.get("quality", "unknown"))).lower() in {"good", "ok"}:
        try:
            value = float(point.get("v", point.get("value")))
        except (TypeError, ValueError):
            pass
    age = None
    try:
        stamp = datetime.fromisoformat(str(solar.get("timestamp") or view.get("timestamp")).replace("Z", "+00:00"))
        age = max(0.0, (datetime.now(stamp.tzinfo or IST) - stamp).total_seconds())
    except (TypeError, ValueError):
        pass
    fresh = bool(solar.get("online")) and value is not None and age is not None and age <= max_age
    return value, fresh, age


def solar_evidence(
    view: dict[str, Any], max_age: int, stop_below_kw: float
) -> dict[str, Any]:
    solar = view.get("solar") or {}
    point = solar.get("active_power_kw") or {}
    value, fresh, age = solar_sample(view, max_age)
    online = bool(solar.get("online"))
    quality = str(point.get("q", point.get("quality", "unknown"))).lower()
    if not online:
        condition = "offline"
    elif value is None:
        condition = "missing_or_bad_quality"
    elif age is None or age > max_age:
        condition = "stale"
    elif value < stop_below_kw:
        condition = "below_threshold"
    else:
        condition = "healthy"
    return {
        "condition": condition,
        "online": online,
        "quality": quality,
        "value_kw": value,
        "age_seconds": None if age is None else round(age, 3),
        "max_age_seconds": max_age,
        "stop_below_kw": stop_below_kw,
        "fresh": fresh,
        "errors": list(solar.get("errors") or [])[:8],
    }


def target_tracking(item: dict[str, Any], direction: str, target: float) -> bool:
    summary = item.get("summary") or {}
    try:
        set_kw = float(summary.get("pcs_power_setpoint_kw") or 0)
        actual = float(summary.get("pcs_actual_power_kw") or 0)
    except (TypeError, ValueError):
        return False
    expected = -target if direction == "charge" else target
    return abs(set_kw - expected) <= 5 and target * .70 <= abs(actual) <= target * 1.30 and (actual < 0 if direction == "charge" else actual > 0)


def target_tracking_evidence(
    item: dict[str, Any], direction: str, target: float
) -> dict[str, Any]:
    """Return bounded evidence for an active-power tracking decision."""
    summary = item.get("summary") or {}
    runtime = item.get("runtime") or {}
    evidence: dict[str, Any] = {
        "direction": direction,
        "target_kw": target,
        "telemetry_source": item.get("telemetry_source"),
        "errors": list(item.get("errors") or [])[:8],
        "runtime_stage": runtime.get("stage"),
        "runtime_status": runtime.get("run_status"),
    }
    for key in (
        "pcs_power_setpoint_kw",
        "pcs_actual_power_kw",
        "pcs_operating_state",
        "precharge_state",
        "positive_contactor_closed",
        "negative_contactor_closed",
        "pcs_dc_breaker_feedback_closed",
    ):
        evidence[key] = summary.get(key)
    evidence["tracking_ok"] = target_tracking(item, direction, target)
    return evidence


def recoverable_pcs_1024(runtime: dict[str, Any]) -> bool:
    """Only admit the observed, narrow PCS-start state-1024 failure."""
    if str(runtime.get("run_status") or "").lower() != "failed":
        return False
    steps = runtime.get("steps") or []
    pcs_start = next(
        (step for step in steps if isinstance(step, dict) and step.get("key") == "pcs_start"),
        {},
    )
    message = str(pcs_start.get("message") or runtime.get("last_error") or "")
    if "state=1024" not in message or str(pcs_start.get("status")) != "failed":
        return False
    lowered = message.lower()
    allowed_fault_words = all(
        token in lowered
        for token in (
            "module_fault_word_10=none",
            "latched_fault_word_7=none",
        )
    )
    return allowed_fault_words


def fully_stopped(item: dict[str, Any]) -> bool:
    s = item.get("summary") or {}
    try:
        return int(float(s.get("precharge_state") or 0)) == 0 and not s.get("positive_contactor_closed") and not s.get("negative_contactor_closed") and int(float(s.get("pcs_operating_state") or 0)) == 1 and abs(float(s.get("pcs_power_setpoint_kw") or 0)) <= .5 and abs(float(s.get("pcs_actual_power_kw") or 0)) <= 1
    except (TypeError, ValueError):
        return False


def ready_zero(item: dict[str, Any]) -> bool:
    """Return the legacy compact-readback opinion for diagnostics only.

    The control backend's matching ``prepared`` terminal is the authoritative
    result because it is committed only after the live automatic sequence has
    verified PCS configuration, precharge/contactors, DC soft-start and the
    power precheck. Compact telemetry can legitimately lag that transition and
    must never overturn it.
    """
    summary = item.get("summary") or {}
    workflow = item.get("workflow") or {}
    try:
        return (
            int(float(summary.get("precharge_state") or 0)) == 3
            and bool(summary.get("positive_contactor_closed"))
            and bool(summary.get("negative_contactor_closed"))
            and int(float(summary.get("pcs_operating_state") or 0)) in {16, 32, 80}
            and abs(float(summary.get("pcs_power_setpoint_kw") or 0)) <= .5
            and abs(float(summary.get("pcs_actual_power_kw") or 0)) <= 1
            and bool(workflow.get("ready_for_power"))
            and not item.get("errors")
        )
    except (TypeError, ValueError):
        return False


def matching_prepared_runtime(
    item: dict[str, Any], run_id: str, direction: str, target: float
) -> bool:
    runtime = item.get("runtime") or {}
    try:
        requested_power = float(runtime.get("requested_power_kw"))
    except (TypeError, ValueError):
        return False
    return (
        str(runtime.get("run_id") or "") == run_id
        and str(runtime.get("run_status") or "").lower() == "prepared"
        and runtime.get("stage") == "automatic_prepared_zero_power"
        and runtime.get("requested_direction") == direction
        and abs(requested_power - target) <= 0.001
        and not runtime.get("last_error")
    )


def matching_successful_runtime(
    item: dict[str, Any], run_id: str, direction: str, target: float
) -> bool:
    runtime = item.get("runtime") or {}
    try:
        requested_power = float(runtime.get("requested_power_kw"))
    except (TypeError, ValueError):
        return False
    return (
        str(runtime.get("run_id") or "") == run_id
        and str(runtime.get("run_status") or "").lower() == "success"
        and runtime.get("stage") == "automatic_sequence_complete"
        and runtime.get("requested_direction") == direction
        and abs(requested_power - target) <= 0.001
        and not runtime.get("last_error")
    )


class Supervisor:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.phase = "starting"
        self.direction: str | None = None
        self.active_pairs: set[str] = set()
        self.prepared_pairs: set[str] = set()
        self.prepared_run_ids: dict[str, str] = {}
        self.above_since: float | None = None
        self.below_since: float | None = None
        self.solar_unavailable_since: float | None = None
        self.solar_kw: float | None = None
        self.solar_fresh = False
        self.solar_diagnostic: dict[str, Any] = {}
        self.error: str | None = None
        self.config_revision: str | None = None
        self.safe_stop_requested = False
        self.last_snapshot_success = time.monotonic()
        self.snapshot_failures = 0
        self.snapshot_next_attempt = 0.0
        self.snapshot_unavailable_since: float | None = None
        self.active_since: float | None = None
        self.active_readback_failures: dict[str, int] = {
            pair_id: 0 for pair_id in PAIR_IDS
        }
        self.recovery_attempts: set[tuple[str, str]] = set()
        try:
            previous_status = read_json(STATUS_PATH)
            previous_latch = previous_status.get("fault_latch")
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            previous_latch = None
        self.fault_latch: dict[str, Any] | None = (
            dict(previous_latch) if isinstance(previous_latch, dict) else None
        )
        self.recent_events: list[dict[str, Any]] = []

    def event(self, event_type: str, message: str, **details: Any) -> None:
        item = {
            "timestamp": datetime.now(IST).isoformat(),
            "event": event_type,
            "phase": self.phase,
            "direction": self.direction,
            "message": message,
            "details": details,
        }
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if EVENTS_PATH.exists() and EVENTS_PATH.stat().st_size > 5 * 1024 * 1024:
            rotated = EVENTS_PATH.with_suffix(".jsonl.1")
            if rotated.exists():
                rotated.unlink()
            EVENTS_PATH.replace(rotated)
        with EVENTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.recent_events = (self.recent_events + [item])[-30:]
        log(f"{event_type}: {message}")

    def publish(self, message: str, **extra: Any) -> None:
        atomic_write(STATUS_PATH, {"version": 2, "build": "phase5.8.1.8-runtime-resilience", "running": True, "phase": self.phase, "direction": self.direction, "prepared_pairs": sorted(self.prepared_pairs), "prepared_run_ids": dict(self.prepared_run_ids), "active_pairs": sorted(self.active_pairs), "solar_kw": self.solar_kw, "solar_fresh": self.solar_fresh, "solar_diagnostic": self.solar_diagnostic, "solar_telemetry_loss_seconds": None if self.solar_unavailable_since is None else round(max(0.0, time.monotonic() - self.solar_unavailable_since), 1), "solar_telemetry_loss_safe_stop_seconds": SOLAR_TELEMETRY_LOSS_SAFE_STOP_SECONDS, "message": message, "error": self.error, "fault_latch": self.fault_latch, "safe_stop_requested": self.safe_stop_requested, "active_readback_failures": dict(self.active_readback_failures), "snapshot_failures": self.snapshot_failures, "snapshot_loss_seconds": None if self.snapshot_unavailable_since is None else round(max(0.0, time.monotonic() - self.snapshot_unavailable_since), 1), "snapshot_loss_safe_stop_seconds": SNAPSHOT_LOSS_SAFE_STOP_SECONDS, "recent_events": self.recent_events, "timestamp": datetime.now(IST).isoformat(), **extra})

    def latch_fault(self, plan: dict[str, Any], fault_phase: str, error: Exception) -> None:
        reason = str(error)
        self.fault_latch = {
            "window": plan["active_by"].isoformat(),
            "direction": str(plan["direction"]),
            "fault_phase": fault_phase,
            "reason": reason,
            "latched_at": datetime.now(IST).isoformat(),
        }
        self.error = reason
        self.phase = "fault_latched"
        self.event(
            "automation_fault_latched",
            f"{fault_phase} fault latched; automatic retries inhibited",
            **self.fault_latch,
        )
        self.publish("Automation fault latched; change/save configuration to reset")

    def all_status(self) -> dict[str, Any]:
        now = time.monotonic()
        if now < self.snapshot_next_attempt:
            remaining = self.snapshot_next_attempt - now
            raise SnapshotUnavailable(
                f"scheduler snapshot circuit open; retry in {remaining:.1f}s"
            )
        try:
            payload = self.gateway.request(
                "GET",
                "/api/internal/control-sequence/automation-snapshot",
                timeout=4,
            )
        except Exception as error:
            if self.snapshot_unavailable_since is None:
                self.snapshot_unavailable_since = time.monotonic()
            self.snapshot_failures += 1
            base = min(30.0, 2.0 * (2 ** min(self.snapshot_failures - 1, 4)))
            self.snapshot_next_attempt = time.monotonic() + base + random.uniform(0, 0.5)
            raise SnapshotUnavailable(str(error)) from error
        previous_failures = self.snapshot_failures
        unavailable_since = self.snapshot_unavailable_since
        self.snapshot_failures = 0
        self.snapshot_next_attempt = 0.0
        self.snapshot_unavailable_since = None
        self.last_snapshot_success = time.monotonic()
        if previous_failures:
            self.event(
                "scheduler_snapshot_restored",
                "Scheduler snapshot communication restored",
                previous_failures=previous_failures,
                unavailable_seconds=None if unavailable_since is None else round(
                    max(0.0, time.monotonic() - unavailable_since), 1
                ),
            )
        return payload

    def handle_snapshot_unavailable(self, error: SnapshotUnavailable) -> None:
        """Defer a transient control-plane failure without dropping a healthy run.

        Physical/readback and solar faults retain their existing immediate or
        independently-debounced safe-stop paths. Only loss of the local,
        cache-only scheduler snapshot is tolerated here, and only for a bounded
        interval while the scheduler owns prepared or active pairs.
        """
        now = time.monotonic()
        if self.snapshot_unavailable_since is None:
            self.snapshot_unavailable_since = now
        elapsed = max(0.0, now - self.snapshot_unavailable_since)
        self.error = str(error)
        self.phase = "snapshot_degraded"
        log(
            "Scheduler snapshot temporarily unavailable: "
            f"{error}; loss={elapsed:.1f}s/{SNAPSHOT_LOSS_SAFE_STOP_SECONDS:.1f}s"
        )
        self.publish(
            "Scheduler snapshot temporarily unavailable; active operation retained",
            snapshot_retryable=True,
        )
        if (
            (self.active_pairs or self.prepared_pairs)
            and elapsed >= SNAPSHOT_LOSS_SAFE_STOP_SECONDS
            and not self.safe_stop_requested
        ):
            self.event(
                "scheduler_snapshot_loss_expired",
                "Bounded scheduler snapshot loss expired",
                unavailable_seconds=round(elapsed, 1),
                limit_seconds=SNAPSHOT_LOSS_SAFE_STOP_SECONDS,
            )
            self.stop_all(
                "Scheduler snapshot unavailable beyond bounded safety delay"
            )

    def stop_all(self, reason: str) -> None:
        self.phase = "safe_stopping"
        if not self.safe_stop_requested:
            self.event("safe_stop_started", reason)
        self.publish(reason)
        if not self.safe_stop_requested:
            # Mark before submitting. A client timeout is ambiguous: the
            # gateway may already have accepted the operation, so never submit
            # another Safe Stop merely because the response was lost.
            self.safe_stop_requested = True
            try:
                self.gateway.request("POST", "/api/control-sequence/safe-stop-all", {"confirmation": "EXECUTE_STAGE_WRITE", "open_bms": True}, timeout=30)
            except SnapshotUnavailable as error:
                try:
                    self.handle_snapshot_unavailable(error)
                except Exception as handling_error:
                    self.error = str(handling_error)
                    log(f"Snapshot degradation handling failed: {handling_error}")
            except Exception as error:
                log(f"Safe Stop submission response unavailable; monitoring existing operation: {error}")
        deadline = time.monotonic() + 720
        read_failures = 0
        while time.monotonic() < deadline:
            try:
                payload = self.all_status()
                read_failures = 0
            except Exception as error:
                read_failures += 1
                self.publish(
                    "Safe Stop submitted; verification temporarily unavailable",
                    safe_stop_read_error=str(error),
                    safe_stop_read_failures=read_failures,
                )
                time.sleep(min(10.0, 1.5 * (2 ** min(read_failures, 3))))
                continue
            progress = payload.get("safe_stop_all") or {}
            status = str(progress.get("status") or "running").lower()
            self.publish(f"Safe Stop All: {status}", safe_stop_all=progress)
            if status == "failed":
                raise RuntimeError(f"Safe Stop All failed: {progress}")
            pairs = payload.get("by_pair_id") or {}
            if status == "success" and all(fully_stopped(pairs.get(pair_id) or {}) for pair_id in PAIR_IDS):
                self.active_pairs.clear()
                self.prepared_pairs.clear()
                self.prepared_run_ids.clear()
                self.active_since = None
                self.active_readback_failures = {pair_id: 0 for pair_id in PAIR_IDS}
                self.safe_stop_requested = False
                self.direction = None
                self.phase = "waiting"
                self.event("safe_stop_complete", "All four pairs verified safely stopped")
                self.publish("All four pairs verified safely stopped")
                return
            time.sleep(2)
        raise TimeoutError("Safe Stop All did not verify within 12 minutes")

    def _wait_for_runtime(
        self,
        pair_id: str,
        run_id: str,
        direction: str,
        target: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        final: dict[str, Any] = {}
        status_unavailable_since: float | None = None
        status_failures = 0
        while time.monotonic() < deadline:
            try:
                final = self.gateway.request(
                    "GET", f"/api/control-sequence/{pair_id}/status/compact"
                )
            except RuntimeError as error:
                if not retryable_http_capacity_error(error):
                    raise
                status_failures += 1
                status_unavailable_since = status_unavailable_since or time.monotonic()
                unavailable_for = time.monotonic() - status_unavailable_since
                self.publish(
                    f"{pair_id}: control status temporarily busy; preserving run",
                    current_pair=pair_id,
                    control_status_failures=status_failures,
                    control_status_unavailable_seconds=round(unavailable_for, 1),
                    control_status_retry_seconds=CONTROL_STATUS_RETRY_SECONDS,
                )
                if unavailable_for >= CONTROL_STATUS_RETRY_SECONDS:
                    raise RuntimeError(
                        f"{pair_id} control status remained unavailable for "
                        f"{unavailable_for:.1f}s: {error}"
                    ) from error
                time.sleep(1)
                continue
            if status_unavailable_since is not None:
                self.event(
                    "control_status_recovered",
                    f"{pair_id} control status recovered without changing run",
                    pair_id=pair_id,
                    run_id=run_id,
                    transient_failures=status_failures,
                    unavailable_seconds=round(
                        time.monotonic() - status_unavailable_since, 1
                    ),
                )
                status_unavailable_since = None
                status_failures = 0
            runtime = final.get("runtime") or {}
            incoming_run_id = str(runtime.get("run_id") or "")
            self.publish(
                f"{pair_id}: {runtime.get('stage') or runtime.get('run_status')}",
                current_pair=pair_id,
                current_step=runtime.get("stage"),
            )
            if incoming_run_id != run_id:
                time.sleep(2)
                continue
            run_status = str(runtime.get("run_status") or "").lower()
            if run_status in {"failed", "aborted"}:
                raise RuntimeError(f"{pair_id} preparation failed: {runtime}")
            if matching_prepared_runtime(final, run_id, direction, target):
                return final
            time.sleep(2)
        raise TimeoutError(f"{pair_id} zero-power preparation timed out")

    def _recover_pcs_1024(
        self,
        pair_id: str,
        direction: str,
        window_key: str,
        failed_runtime: dict[str, Any],
    ) -> None:
        recovery_key = (window_key, pair_id)
        if recovery_key in self.recovery_attempts:
            raise RuntimeError(f"{pair_id} PCS state-1024 recovery already attempted")
        if not recoverable_pcs_1024(failed_runtime):
            raise RuntimeError(f"{pair_id} failure is not eligible for PCS state-1024 recovery")
        self.recovery_attempts.add(recovery_key)
        self.event(
            "pcs_1024_recovery_started",
            f"Bounded PCS state-1024 recovery started for {pair_id}",
            pair_id=pair_id,
            window=window_key,
            failed_run_id=failed_runtime.get("run_id"),
            failure=failed_runtime.get("last_error"),
        )

        # Field recovery requires the failed PCS to be reset to the verified
        # stopped baseline before asking it to start at zero power again.
        # Starting the zero-power sequence while state 1024 is still latched
        # only repeats the same startup failure.
        self.event(
            "pcs_1024_recovery_reset_started",
            f"Resetting {pair_id} to a verified stopped baseline",
            pair_id=pair_id,
        )
        self.gateway.request(
            "POST",
            f"/api/control-sequence/{pair_id}/safe-stop",
            {"confirmation": "EXECUTE_STAGE_WRITE", "open_bms": True},
            timeout=30,
        )
        deadline = time.monotonic() + RECOVERY_TIMEOUT_SECONDS
        reset_status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            reset_status = self.gateway.request(
                "GET", f"/api/control-sequence/{pair_id}/status/compact"
            )
            if fully_stopped(reset_status):
                reset_summary = reset_status.get("summary") or {}
                self.event(
                    "pcs_1024_recovery_reset_complete",
                    f"{pair_id} verified stopped before zero-power recovery",
                    pair_id=pair_id,
                    final_state=reset_summary.get("pcs_operating_state"),
                )
                break
            time.sleep(2)
        else:
            raise TimeoutError(
                f"{pair_id} PCS state-1024 pre-recovery safe-stop timed out"
            )

        accepted = self.gateway.request(
            "POST",
            f"/api/control-sequence/{pair_id}/automatic-start",
            {
                "direction": direction,
                "power_kw": 0.0,
                "ramp_step_kw": 0.0,
                "ramp_interval_seconds": 1,
                "prepare_only": True,
                "confirmation": "EXECUTE_AUTOMATIC_SEQUENCE",
            },
            timeout=30,
        )
        recovery_run_id = str(accepted.get("run_id") or "")
        if not recovery_run_id:
            raise RuntimeError(f"{pair_id} recovery acceptance omitted run_id")
        self._wait_for_runtime(
            pair_id,
            recovery_run_id,
            direction,
            0.0,
            RECOVERY_TIMEOUT_SECONDS,
        )
        self.event(
            "pcs_1024_recovery_zero_prepared",
            f"{pair_id} reached zero-power readiness during recovery",
            pair_id=pair_id,
            recovery_run_id=recovery_run_id,
        )
        self.gateway.request(
            "POST",
            f"/api/control-sequence/{pair_id}/safe-stop",
            {"confirmation": "EXECUTE_STAGE_WRITE", "open_bms": True},
            timeout=30,
        )
        deadline = time.monotonic() + RECOVERY_TIMEOUT_SECONDS
        final: dict[str, Any] = {}
        while time.monotonic() < deadline:
            final = self.gateway.request(
                "GET", f"/api/control-sequence/{pair_id}/status/compact"
            )
            if fully_stopped(final):
                summary = final.get("summary") or {}
                if int(float(summary.get("pcs_operating_state") or 0)) == 1024:
                    raise RuntimeError(f"{pair_id} remained in PCS state 1024 after recovery")
                self.event(
                    "pcs_1024_recovery_complete",
                    f"{pair_id} PCS state-1024 recovery completed",
                    pair_id=pair_id,
                    recovery_run_id=recovery_run_id,
                    final_state=summary.get("pcs_operating_state"),
                )
                return
            time.sleep(2)
        raise TimeoutError(f"{pair_id} PCS state-1024 recovery safe-stop timed out")

    def prepare_pairs(
        self, config: dict[str, Any], direction: str, window_key: str
    ) -> None:
        preparation_started = time.monotonic()
        self.phase, self.direction = f"preparing_{direction}", direction
        self.event("preparation_started", f"Zero-power preparation started for {direction}")
        self.publish(f"Preparing enabled pairs at zero power for {direction}")
        baseline = self.all_status().get("by_pair_id") or {}
        if any(not fully_stopped(baseline.get(pair_id) or {}) for pair_id in PAIR_IDS):
            self.stop_all("Preparation requires a verified safe baseline")
            self.phase, self.direction = f"preparing_{direction}", direction
        try:
            for pair_id in PAIR_IDS:
                setting = (config.get("pairs") or {}).get(pair_id) or {}
                if not setting.get("enabled"):
                    continue
                target = float(setting[f"{direction}_kw"])
                pair_started = time.monotonic()
                self.event("pair_preparation_started", f"Preparing {pair_id}", pair_id=pair_id)
                final: dict[str, Any] = {}
                for attempt in range(2):
                    accepted = self.gateway.request(
                        "POST",
                        f"/api/control-sequence/{pair_id}/automatic-start",
                        {
                            "direction": direction,
                            "power_kw": target,
                            "ramp_step_kw": target,
                            "ramp_interval_seconds": 1,
                            "prepare_only": True,
                            "confirmation": "EXECUTE_AUTOMATIC_SEQUENCE",
                        },
                        timeout=30,
                    )
                    run_id = str(accepted.get("run_id") or "")
                    if not run_id:
                        raise RuntimeError(f"{pair_id} preparation acceptance omitted run_id")
                    self.event(
                        "pair_preparation_accepted",
                        f"{pair_id} preparation accepted",
                        pair_id=pair_id,
                        run_id=run_id,
                        attempt=attempt + 1,
                    )
                    try:
                        final = self._wait_for_runtime(
                            pair_id, run_id, direction, target, 600
                        )
                        break
                    except RuntimeError:
                        failed = self.gateway.request(
                            "GET", f"/api/control-sequence/{pair_id}/status/compact"
                        )
                        failed_runtime = failed.get("runtime") or {}
                        if attempt or not recoverable_pcs_1024(failed_runtime):
                            raise
                        self._recover_pcs_1024(
                            pair_id, direction, window_key, failed_runtime
                        )
                self.prepared_pairs.add(pair_id)
                self.prepared_run_ids[pair_id] = run_id
                self.event(
                    "pair_prepared",
                    f"{pair_id} ready at zero power",
                    pair_id=pair_id,
                    run_id=run_id,
                    verification_source="matching_automatic_prepared_runtime",
                    compact_ready_zero_diagnostic=ready_zero(final),
                    duration_seconds=round(time.monotonic() - pair_started, 1),
                )
                self.publish(f"{pair_id} ready at zero power")
        except Exception:
            self.stop_all(f"{direction} zero-power preparation failure")
            raise
        if not self.prepared_pairs:
            raise RuntimeError("no enabled pair was prepared")
        self.phase = f"ready_{direction}"
        self.event(
            "preparation_complete",
            "All enabled pairs ready at zero power",
            pairs=sorted(self.prepared_pairs),
            duration_seconds=round(time.monotonic() - preparation_started, 1),
        )
        self.publish(f"All enabled pairs ready at zero power for {direction}")

    def dispatch_power(self, config: dict[str, Any], direction: str, active_by: datetime) -> None:
        enabled = {
            pair_id
            for pair_id in PAIR_IDS
            if ((config.get("pairs") or {}).get(pair_id) or {}).get("enabled")
        }
        if self.prepared_pairs != enabled:
            raise RuntimeError(f"power dispatch blocked: prepared={sorted(self.prepared_pairs)}, enabled={sorted(enabled)}")
        self.phase, self.direction = f"dispatching_{direction}", direction
        self.event("power_dispatch_started", f"Dispatching {direction} targets", active_by=active_by.isoformat())
        self.publish(f"Dispatching {direction} targets to all prepared pairs")
        try:
            for pair_id in sorted(enabled):
                target = float(config["pairs"][pair_id][f"{direction}_kw"])
                accepted = self.gateway.request(
                    "POST", f"/api/control-sequence/{pair_id}/automatic-dispatch",
                    {
                        "direction": direction,
                        "power_kw": target,
                        "ramp_step_kw": target,
                        "ramp_interval_seconds": 1,
                        "prepare_only": False,
                        "confirmation": "EXECUTE_AUTOMATIC_SEQUENCE",
                    },
                    timeout=30,
                )
                dispatch_run_id = str(accepted.get("run_id") or "")
                if dispatch_run_id != self.prepared_run_ids.get(pair_id):
                    raise RuntimeError(
                        f"{pair_id} dispatch run_id mismatch: prepared="
                        f"{self.prepared_run_ids.get(pair_id)!r}, dispatch={dispatch_run_id!r}"
                    )
                self.event("pair_power_commanded", f"{pair_id} commanded to {target:.1f} kW {direction}", pair_id=pair_id, power_kw=target)
            deadline = time.monotonic() + 240
            read_failures = 0
            while time.monotonic() < deadline:
                try:
                    pairs = self.all_status().get("by_pair_id") or {}
                    read_failures = 0
                except Exception as error:
                    read_failures += 1
                    self.publish(
                        "Dispatch accepted; verification temporarily unavailable",
                        verification_error=str(error),
                        verification_read_failures=read_failures,
                    )
                    time.sleep(min(10.0, 1.5 * (2 ** min(read_failures, 3))))
                    continue
                verified: set[str] = set()
                for pair_id in enabled:
                    item = pairs.get(pair_id) or {}
                    runtime = item.get("runtime") or {}
                    if str(runtime.get("run_id") or "") != self.prepared_run_ids[pair_id]:
                        continue
                    run_status = str(runtime.get("run_status") or "").lower()
                    if run_status in {"failed", "aborted"}:
                        raise RuntimeError(f"{pair_id} dispatch failed: {runtime}")
                    target = float(config["pairs"][pair_id][f"{direction}_kw"])
                    if matching_successful_runtime(
                        item, self.prepared_run_ids[pair_id], direction, target
                    ):
                        verified.add(pair_id)
                self.active_pairs = verified
                self.publish(f"Power tracking verified for {len(verified)}/{len(enabled)} pairs", active_by=active_by.isoformat())
                if verified == enabled:
                    self.phase = f"active_{direction}"
                    self.active_since = time.monotonic()
                    self.active_readback_failures = {
                        pair_id: 0 for pair_id in PAIR_IDS
                    }
                    boundary_delta = round((datetime.now(IST) - active_by).total_seconds(), 1)
                    timing = (
                        f"{abs(boundary_delta):.1f}s late"
                        if boundary_delta > 0
                        else f"{abs(boundary_delta):.1f}s early"
                    )
                    self.event(
                        "all_pairs_active",
                        f"All enabled pairs active in {direction} ({timing})",
                        pairs=sorted(verified),
                        active_by=active_by.isoformat(),
                        boundary_delta_seconds=boundary_delta,
                    )
                    self.publish(f"All enabled pairs active in {direction} ({timing})")
                    return
                time.sleep(2)
            raise TimeoutError("not all pairs verified target power before dispatch timeout")
        except Exception:
            self.stop_all(f"{direction} power dispatch failure")
            raise

    def reconcile(self, config: dict[str, Any], direction: str) -> None:
        try:
            pairs = self.all_status().get("by_pair_id") or {}
        except Exception as error:
            self.publish(
                "Active control verification temporarily unavailable",
                verification_error=str(error),
            )
            return
        if self.active_since is not None and (
            time.monotonic() - self.active_since < ACTIVE_READBACK_GRACE_SECONDS
        ):
            return
        for pair_id in sorted(self.active_pairs):
            setting = (config.get("pairs") or {}).get(pair_id) or {}
            target = float(setting.get(f"{direction}_kw") or 0)
            item = pairs.get(pair_id) or {}
            if setting.get("enabled") and target_tracking(item, direction, target):
                if self.active_readback_failures[pair_id]:
                    self.event(
                        "active_readback_recovered",
                        f"Active readback recovered for {pair_id}",
                        pair_id=pair_id,
                        previous_failures=self.active_readback_failures[pair_id],
                        evidence=target_tracking_evidence(item, direction, target),
                    )
                self.active_readback_failures[pair_id] = 0
                continue
            evidence = target_tracking_evidence(item, direction, target)
            try:
                confirmed = self.gateway.request(
                    "GET",
                    f"/api/control-sequence/{pair_id}/status/compact?fresh=true&wait_seconds=8",
                    timeout=12,
                )
            except Exception as error:
                self.event(
                    "active_readback_confirmation_deferred",
                    f"Fresh confirmation unavailable for {pair_id}",
                    pair_id=pair_id,
                    cached_evidence=evidence,
                    error=str(error),
                )
                continue
            confirmed_evidence = target_tracking_evidence(
                confirmed, direction, target
            )
            if confirmed_evidence["tracking_ok"]:
                self.active_readback_failures[pair_id] = 0
                self.event(
                    "active_readback_transient",
                    f"Transient cached readback mismatch cleared for {pair_id}",
                    pair_id=pair_id,
                    cached_evidence=evidence,
                    confirmed_evidence=confirmed_evidence,
                )
                continue
            count = self.active_readback_failures[pair_id] + 1
            self.active_readback_failures[pair_id] = count
            self.event(
                "active_readback_suspect",
                f"Active readback mismatch {count}/{ACTIVE_READBACK_CONFIRMATIONS} for {pair_id}",
                pair_id=pair_id,
                consecutive_failures=count,
                required_confirmations=ACTIVE_READBACK_CONFIRMATIONS,
                cached_evidence=evidence,
                confirmed_evidence=confirmed_evidence,
            )
            if count >= ACTIVE_READBACK_CONFIRMATIONS:
                self.stop_all(
                    f"Confirmed active readback loss for {pair_id} "
                    f"({count} consecutive fresh confirmations)"
                )
                return

    def cycle(self, config: dict[str, Any], view: dict[str, Any], next_reconcile: float) -> float:
        solar = config.get("solar") or {}
        max_solar_age = int(solar.get("max_age_seconds", 15))
        stop_below_kw = float(solar.get("stop_below_kw", 80))
        self.solar_kw, self.solar_fresh, age = solar_sample(view, max_solar_age)
        self.solar_diagnostic = solar_evidence(
            view, max_solar_age, stop_below_kw
        )
        plan, now = schedule_plan(config, datetime.now(IST)), time.monotonic()
        if plan is None:
            self.above_since = self.below_since = None
            self.solar_unavailable_since = None
            if self.active_pairs or self.prepared_pairs:
                self.event("window_ended", "Configured operating/preparation window ended")
                self.stop_all("Schedule disabled or window ended")
            self.phase = "disabled" if not config.get("enabled") else "waiting_window"
            self.direction = None
            self.fault_latch = None
            self.recovery_attempts.clear()
            self.error = None
            self.publish("Automation disabled" if not config.get("enabled") else "Waiting for next window")
            return next_reconcile

        wanted = str(plan["direction"])
        window_key = plan["active_by"].isoformat()
        if self.fault_latch is not None:
            same_window = (
                self.fault_latch.get("window") == window_key
                and self.fault_latch.get("direction") == wanted
            )
            if same_window:
                self.phase = "fault_latched"
                self.direction = wanted
                self.error = str(self.fault_latch.get("reason") or "automation fault")
                self.publish(
                    "Automation fault latched; automatic retries inhibited",
                    active_by=window_key,
                )
                return next_reconcile
            self.fault_latch = None
            self.error = None

        remaining_window_seconds = (plan["stop_at"] - datetime.now(IST)).total_seconds()
        if (
            not self.active_pairs
            and not self.prepared_pairs
            and plan["mode"] == "operate"
            and remaining_window_seconds < MIN_RESTART_REMAINING_SECONDS
        ):
            self.above_since = None
            self.phase = "waiting_window_guard"
            self.direction = wanted
            self.publish(
                "Restart inhibited: insufficient operating window remains",
                remaining_window_seconds=round(remaining_window_seconds, 1),
                minimum_restart_remaining_seconds=MIN_RESTART_REMAINING_SECONDS,
                stop_at=plan["stop_at"].isoformat(),
            )
            return next_reconcile
        if self.direction and wanted != self.direction:
            self.event("direction_changed", f"Schedule direction changed to {wanted}")
            self.stop_all("Operating direction changed")

        solar_condition = str(self.solar_diagnostic.get("condition") or "unknown")
        confirmed_low = solar_condition == "below_threshold"
        telemetry_unavailable = solar_condition in {
            "offline",
            "missing_or_bad_quality",
            "stale",
            "unknown",
        }
        if self.active_pairs or self.prepared_pairs:
            if confirmed_low:
                self.solar_unavailable_since = None
                self.below_since = self.below_since or now
                elapsed = now - self.below_since
                self.phase = "qualifying_solar_loss"
                self.publish(
                    "Fresh solar below stop threshold",
                    solar_age_seconds=age,
                    stop_in_seconds=max(
                        0, int(solar.get("stop_hold_seconds", 30) - elapsed)
                    ),
                )
                if elapsed >= int(solar.get("stop_hold_seconds", 30)):
                    self.event(
                        "solar_loss",
                        "Confirmed low-solar delay expired",
                        solar_kw=self.solar_kw,
                        solar_fresh=self.solar_fresh,
                        solar_diagnostic=self.solar_diagnostic,
                    )
                    self.stop_all("Confirmed low-solar delay expired")
                    return next_reconcile
                if self.prepared_pairs and not self.active_pairs:
                    return next_reconcile
            elif telemetry_unavailable:
                self.below_since = None
                self.solar_unavailable_since = self.solar_unavailable_since or now
                unavailable_for = now - self.solar_unavailable_since
                self.phase = "solar_telemetry_degraded"
                self.publish(
                    "Solar telemetry temporarily unavailable; operation preserved",
                    solar_age_seconds=age,
                    telemetry_condition=solar_condition,
                    telemetry_loss_seconds=round(unavailable_for, 1),
                    safe_stop_in_seconds=max(
                        0,
                        int(
                            SOLAR_TELEMETRY_LOSS_SAFE_STOP_SECONDS
                            - unavailable_for
                        ),
                    ),
                )
                if unavailable_for >= SOLAR_TELEMETRY_LOSS_SAFE_STOP_SECONDS:
                    self.event(
                        "solar_telemetry_loss",
                        "Sustained solar telemetry loss threshold expired",
                        solar_kw=self.solar_kw,
                        solar_fresh=self.solar_fresh,
                        unavailable_seconds=round(unavailable_for, 1),
                        threshold_seconds=SOLAR_TELEMETRY_LOSS_SAFE_STOP_SECONDS,
                        solar_diagnostic=self.solar_diagnostic,
                    )
                    self.stop_all("Sustained solar telemetry loss")
                    return next_reconcile
                # Never dispatch prepared pairs using stale solar evidence.
                # Active pairs continue while the bounded telemetry-loss timer
                # runs and all independent PCS/BMS checks remain active.
                if self.prepared_pairs and not self.active_pairs:
                    return next_reconcile
            else:
                if self.solar_unavailable_since is not None:
                    self.event(
                        "solar_telemetry_recovered",
                        "Solar telemetry recovered without restarting pairs",
                        unavailable_seconds=round(
                            now - self.solar_unavailable_since, 1
                        ),
                        solar_kw=self.solar_kw,
                    )
                self.below_since = None
                self.solar_unavailable_since = None

        if self.active_pairs:
            if self.phase not in {
                "qualifying_solar_loss",
                "solar_telemetry_degraded",
            }:
                self.phase = f"active_{wanted}"
            if now >= next_reconcile:
                self.reconcile(config, wanted)
                next_reconcile = now + 30
            active_message = (
                "Solar telemetry temporarily unavailable; operation preserved"
                if telemetry_unavailable
                else "Fresh solar below stop threshold; confirming loss"
                if confirmed_low
                else f"{wanted.capitalize()} active"
            )
            self.publish(active_message, solar_age_seconds=age, active_by=plan["active_by"].isoformat(), stop_at=plan["stop_at"].isoformat())
            return next_reconcile

        if self.prepared_pairs:
            self.direction = wanted
            if plan["mode"] == "operate":
                try:
                    self.dispatch_power(config, wanted, plan["active_by"])
                except Exception as error:
                    self.latch_fault(plan, "power_dispatch", error)
                    raise
            else:
                self.phase = f"ready_{wanted}"
                remaining = max(0, int((plan["dispatch_at"] - datetime.now(IST)).total_seconds()))
                self.publish("All enabled pairs ready at zero power; waiting for dispatch", dispatch_in_seconds=remaining, active_by=plan["active_by"].isoformat())
            return next_reconcile

        self.direction, self.below_since = wanted, None
        self.solar_unavailable_since = None
        high = self.solar_fresh and self.solar_kw is not None and self.solar_kw > float(solar.get("start_above_kw", 100))
        if high:
            self.above_since = self.above_since or now
            elapsed = now - self.above_since
            self.phase = "qualifying_solar"
            self.publish("Solar qualification in progress before zero-power preparation", solar_age_seconds=age, start_in_seconds=max(0, int(solar.get("start_hold_seconds", 60) - elapsed)), active_by=plan["active_by"].isoformat())
            if elapsed >= int(solar.get("start_hold_seconds", 60)):
                self.above_since = None
                self.event("solar_qualified", "Solar start qualification completed", solar_kw=self.solar_kw)
                try:
                    self.prepare_pairs(config, wanted, window_key)
                except Exception as error:
                    self.latch_fault(plan, "zero_power_preparation", error)
                    raise
        else:
            self.above_since = None
            self.phase = "waiting_solar"
            self.publish("Waiting for fresh solar above start threshold", solar_age_seconds=age, active_by=plan["active_by"].isoformat())
        return next_reconcile

    def run(self) -> None:
        self.gateway.wait_ready()
        initial_config = read_json(CONFIG_PATH)
        if initial_config.get("enabled"):
            initial_pairs = self.all_status().get("by_pair_id") or {}
            if any(not fully_stopped(initial_pairs.get(pair_id) or {}) for pair_id in PAIR_IDS):
                self.stop_all("Enabled automation startup reconciliation")
        self.publish("Automation supervisor started")
        next_reconcile = 0.0
        while not STOP:
            try:
                config = read_json(CONFIG_PATH)
                revision = json.dumps(config, sort_keys=True)
                if self.config_revision and revision != self.config_revision and self.fault_latch:
                    self.event(
                        "automation_fault_reset",
                        "Configuration changed; fault latch cleared for operator-requested retry",
                    )
                    self.fault_latch = None
                    self.error = None
                if self.config_revision and revision != self.config_revision and (self.active_pairs or self.prepared_pairs):
                    self.event("configuration_changed", "Configuration changed while hardware was prepared or active")
                    self.stop_all("Configuration changed while active")
                self.config_revision = revision
                # A same-window fault latch is terminal until an operator saves
                # changed configuration or the window ends. Do not keep
                # requesting control-view and do not reissue Safe Stop.
                if self.fault_latch:
                    plan = schedule_plan(config, datetime.now(IST))
                    if plan is not None and (
                        self.fault_latch.get("window") == plan["active_by"].isoformat()
                        and self.fault_latch.get("direction") == plan["direction"]
                    ):
                        self.phase = "fault_latched"
                        self.publish("Automation fault latched; automatic retries inhibited")
                        time.sleep(POLL_SECONDS)
                        continue
                view = self.all_status()
                next_reconcile = self.cycle(config, view, next_reconcile)
                self.error = None
            except Exception as error:
                self.error = str(error)
                self.phase = "fault_latched" if self.fault_latch else "fault"
                log(f"Supervisor cycle failed: {error}")
                self.publish(
                    "Automation fault latched; retries inhibited"
                    if self.fault_latch
                    else "Automation fault; retrying"
                )
                if (self.active_pairs or self.prepared_pairs) and not self.safe_stop_requested:
                    try:
                        self.stop_all("Automation supervisor fault")
                    except Exception as stop_error:
                        log(f"CRITICAL Safe Stop All retry required: {stop_error}")
            time.sleep(POLL_SECONDS)


def main() -> int:
    global STOP
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another scheduler owns the lock")
        return 2
    lock.write(str(os.getpid()))
    lock.flush()
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("STOP", True))
    signal.signal(signal.SIGINT, lambda *_: globals().__setitem__("STOP", True))
    supervisor = Supervisor(Gateway())
    try:
        supervisor.run()
        return 0
    finally:
        if supervisor.active_pairs or supervisor.prepared_pairs:
            supervisor.stop_all("Automation service stopping")
        supervisor.phase = "stopped"
        supervisor.publish("Automation supervisor stopped")


if __name__ == "__main__":
    raise SystemExit(main())
