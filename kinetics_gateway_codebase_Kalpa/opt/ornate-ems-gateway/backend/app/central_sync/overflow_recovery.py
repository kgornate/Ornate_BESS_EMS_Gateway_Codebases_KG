from __future__ import annotations
from contextlib import closing

import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .archive_transport import AdaptivePolicy, DedicatedArchiveClient, ArchiveUploader, load_settings
from .recovery_budget import BudgetPolicy, ByteBudget, load_budget_settings
from .client import CentralBackendClient
from .config import CentralSyncConfig
from .outbox import OutboxStore
from .status import safe_write_json, utc_now_iso
from .transport_control import TransportPauseController
from .uploader import CentralSyncUploader

log = logging.getLogger(__name__)


@dataclass
class RecoveryCounters:
    archive_selection_total_ms: float = 0.0
    archive_store_open_total_ms: float = 0.0
    archive_housekeeping_total_ms: float = 0.0
    archive_session_reuse_count: int = 0
    scan_count: int = 0
    request_cycle_count: int = 0
    archive_accepted_count: int = 0
    archive_already_processed_count: int = 0
    archive_retryable_rejected_count: int = 0
    archive_permanent_rejected_count: int = 0
    archive_missing_ack_count: int = 0
    archive_accepted_bytes: int = 0
    archive_accepted_records: int = 0
    archive_duplicate_bytes: int = 0
    archive_ack_metric_error_count: int = 0
    archive_last_http_duration_ms: float = 0.0
    archive_http_duration_total_ms: float = 0.0
    archive_http_duration_max_ms: float = 0.0
    archive_last_request_bytes: int = 0
    archive_http_attempt_count: int = 0
    archive_http_success_count: int = 0
    archive_http_failure_count: int = 0
    archive_active_requests: int = 0
    archive_peak_active_requests: int = 0
    deferred_before_http_count: int = 0
    last_defer_reason: str | None = None
    bounded_opportunity_count: int = 0
    pause_opportunity_count: int = 0
    pause_transport_count: int = 0
    pause_live_count: int = 0
    pause_memory_count: int = 0
    pause_load_count: int = 0
    archive_open_count: int = 0
    archive_drained_count: int = 0
    retained_drained_count: int = 0
    failure_count: int = 0
    last_archive_path: str | None = None
    last_request_utc: str | None = None
    last_success_utc: str | None = None
    last_pause_reason: str | None = None
    last_error: str | None = None


class _LiveOutboxProbe:
    """Read-only live outbox health probe used by the recovery gate."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"available": False, "reason": "live_outbox_missing"}
        uri = f"file:{self.path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=1000")
            row = conn.execute(
                """SELECT COUNT(*) AS n,MIN(created_epoch) AS oldest
                   FROM outbox_messages
                   WHERE state IN ('pending','retry','inflight')"""
            ).fetchone()
            count = int(row["n"] or 0)
            oldest = row["oldest"]
            return {
                "available": True,
                "unacked_count": count,
                "oldest_age_sec": max(0.0, time.time() - float(oldest)) if oldest else None,
            }
        finally:
            conn.close()


class OverflowRecoveryService:
    """Opportunistically replay only overflow segments produced by this gateway.

    This is intentionally distinct from the legacy historical backlog replayer.
    It only scans ``overflow_recovery.archive_glob`` (default
    ``central_sync_overflow_*.db``), always yields to live traffic, uses its own
    data client and transport lock, with a shared token broker, and never deletes archive databases.

    Therefore:
      * S1-S8 producers keep logging while transport is paused/offline.
      * old live rows may spill into bounded overflow DBs.
      * after transport recovers, live traffic wins first.
      * overflow rows are replayed through the same backend contract, one
        request at a time, only while live health is good.
      * the legacy ~1 GB historical backlog DB is not touched automatically.
    """

    def __init__(
        self,
        config: CentralSyncConfig,
        *,
        transport_lock: asyncio.Lock,
        client: CentralBackendClient | Any,
    ) -> None:
        self.config = config
        self.recovery_config = config.overflow_recovery
        self.transport_lock = asyncio.Lock()
        self.client = None
        self.token_broker = client
        self.settings = load_settings()
        self.budget_settings = load_budget_settings()
        self.byte_budget = ByteBudget(self.budget_settings["burst_bytes"])
        self.policy = BudgetPolicy(self.settings, self.budget_settings, self.byte_budget)
        self.live_probe = _LiveOutboxProbe(config.outbox.path)
        self.pause = TransportPauseController(config.uploader.pause_file)
        self.counters = RecoveryCounters()
        self.started_utc = utc_now_iso()
        self.running = False
        self._stop = asyncio.Event()
        self._session_store = None
        self._session_uploader = None
        self._session_path = None
        self._session_until = 0.0
        self._housekeeping_at = 0.0

    async def stop(self) -> None:
        self._stop.set()

    def _close_session(self):
        store, self._session_store = self._session_store, None
        self._session_uploader = None
        self._session_path = None
        self._session_until = 0.0
        if store is not None:
            store.close()

    async def close(self) -> None:
        self._close_session()
        if self.client is not None:
            client, self.client = self.client, None
            await client.close()

    async def run_forever(self) -> None:
        if not self.recovery_config.enabled:
            log.info("central sync overflow recovery disabled")
            self._write_status()
            return
        self.client = DedicatedArchiveClient(self.config.backend, self.config.identity.gateway_id, self.token_broker, self)
        self.running = True
        self._stop.clear()
        log.info(
            "central sync overflow recovery started dir=%s glob=%s batch=%s bytes=%s pause=%.1fs",
            self.recovery_config.archive_dir,
            self.recovery_config.archive_glob,
            self.recovery_config.max_messages_per_request,
            self.recovery_config.max_request_bytes,
            self.recovery_config.request_pause_sec,
        )
        try:
            while not self._stop.is_set():
                self.counters.scan_count += 1
                allowed, reason, health = await asyncio.to_thread(self._resource_gate)
                if not allowed:
                    self.counters.last_pause_reason = reason
                    self._write_status(extra={"resource_gate": health})
                    await self._sleep(self.policy.delay)
                    continue

                selection_start = time.monotonic()
                archive_path = await asyncio.to_thread(self._next_archive)
                self.counters.archive_selection_total_ms += (time.monotonic()-selection_start)*1000
                if archive_path is None:
                    self.counters.last_pause_reason = "no_overflow_backlog"
                    self._write_status(extra={"resource_gate": health})
                    await self._sleep(self.recovery_config.scan_interval_sec)
                    continue

                try:
                    sent = await self._replay_one(archive_path)
                    self.counters.last_pause_reason = self.counters.last_defer_reason if not sent else None
                    self.counters.last_error = None
                    self._write_status(extra={"resource_gate": health})
                    await self._sleep(
                        0.05 if sent else self.recovery_config.scan_interval_sec
                    )
                except Exception as exc:
                    self._close_session()
                    self.counters.failure_count += 1
                    self.counters.last_error = f"{type(exc).__name__}: {exc}"
                    log.exception("overflow recovery failed archive=%s", archive_path)
                    self._write_status(extra={"resource_gate": health})
                    await self._sleep(self.recovery_config.scan_interval_sec)
        finally:
            await self.close()
            self.running = False
            self._write_status()
            log.info("central sync overflow recovery stopped")

    async def _replay_one(self, archive_path: Path) -> bool:
        self.counters.last_defer_reason = None
        self.counters.last_archive_path = str(archive_path)
        if self._session_store is None or self._session_path != archive_path:
            self._close_session()
            opened_at = time.monotonic()
            store = await asyncio.to_thread(
                OutboxStore, str(archive_path), max_db_size_mb=None,
                min_free_space_mb=0, required_mount_path=None, fail_if_mount_missing=False)
            self.counters.archive_store_open_total_ms += (time.monotonic()-opened_at)*1000
            self.counters.archive_open_count += 1
            replay_cfg = self.config.model_copy(deep=True)
            replay_cfg.status_file = self.recovery_config.uploader_status_file
            replay_cfg.uploader.max_messages_per_request = self.recovery_config.max_messages_per_request
            replay_cfg.uploader.max_request_bytes = self.recovery_config.max_request_bytes
            replay_cfg.uploader.fair_batching_enabled = False
            replay_cfg.uploader.status_interval_sec = max(1.0, self.recovery_config.request_pause_sec)
            replay_cfg.outbox.acked_retention_hours = 0
            try:
                uploader = ArchiveUploader(owner=self, config=replay_cfg, outbox=store,
                    client=self.client, transport_lock=self.transport_lock)
            except BaseException:
                store.close()
                raise
            self._session_store, self._session_uploader = store, uploader
            self._session_path = archive_path
            self._session_until = time.monotonic()+60.0
            self._housekeeping_at = 0.0
        else:
            self.counters.archive_session_reuse_count += 1
        store, uploader = self._session_store, self._session_uploader
        successes_before = uploader.counters.request_success_count
        self.counters.request_cycle_count += 1
        self.counters.last_request_utc = utc_now_iso()
        sent = await uploader.run_once()
        if sent and uploader.counters.request_success_count > successes_before:
            self.counters.last_success_utc = utc_now_iso()
        housekeeping_start = time.monotonic()
        if housekeeping_start >= self._housekeeping_at:
            await asyncio.to_thread(store.cleanup, acked_retention_hours=0,
                dead_letter_retention_days=self.config.outbox.dead_letter_retention_days,
                transport_attempt_retention_days=self.config.outbox.transport_attempt_retention_days)
            self._housekeeping_at = time.monotonic()+60.0
        def remaining_probe():
            with store._lock:
                remaining = store.conn.execute("SELECT 1 FROM outbox_messages WHERE state IN ('pending','retry','inflight') LIMIT 1").fetchone()
                dead = 0 if remaining else store.conn.execute("SELECT COUNT(*) FROM outbox_messages WHERE state='dead'").fetchone()[0]
                return bool(remaining), int(dead)
        remaining, dead = await asyncio.to_thread(remaining_probe)
        self.counters.archive_housekeeping_total_ms += (time.monotonic()-housekeeping_start)*1000
        if not sent or not remaining:
            self._close_session()

        if remaining == 0:
            # Never auto-delete recovery evidence. Persist a marker keyed to the
            # DB's current size/mtime. If the archiver later appends to the same
            # hourly DB, the marker becomes stale and replay resumes.
            self._write_replayed_marker(archive_path, dead_count=dead)
            self.counters.archive_drained_count += 1
            self.counters.retained_drained_count += 1
            log.info(
                "overflow archive fully replayed and retained path=%s dead=%s",
                archive_path,
                dead,
            )
        return sent

    def _live_transport_health(self):
        # Fresh live-uploader status is small; never enumerate archive DBs here.
        try:
            from datetime import datetime, timezone
            data=json.loads(Path(self.config.status_file).read_text())
            stamp=datetime.fromisoformat(data['status_generated_utc'].replace('Z','+00:00'))
            age=(datetime.now(timezone.utc)-stamp).total_seconds()
            if not data.get('running') or not 0<=age<=self.budget_settings['live_status_max_age_sec']:
                return {'available':False}
            u=data['uploader']
            if not isinstance(u.get('request_failure_count'),int): return {'available':False}
            return {'available':True,'last_error':u.get('last_error'),
                    'request_failure_count':u['request_failure_count'],
                    'started_utc':data.get('started_utc'),'status_age_sec':round(age,3)}
        except Exception:
            return {'available':False}

    def _health(self):
        try:load1=float(os.getloadavg()[0])
        except Exception:load1=float("inf")
        try:live=self.live_probe.snapshot()
        except Exception:live={"available":False,"reason":"live_probe_unavailable"}
        return {"transport_paused":self.pause.is_paused(),"live":live,
                "mem_available_mb":_mem_available_mb(),"load1":load1,"cpu_count":os.cpu_count() or 1,"live_transport":self._live_transport_health()}

    def _send_guard(self):
        if self._stop.is_set():return "recovery_stopping"
        return self.policy.hard_reason(self._health(),self.recovery_config)

    def _http_outcome(self,response):
        if response.ok:self.counters.archive_http_success_count+=1
        else:self.counters.archive_http_failure_count+=1
        if response.status_code==429:
            delay=response.retry_after_sec
            if delay is None:delay=self.settings['failure_cooldown_sec']
            self.policy.cooldown(max(1,float(delay)))
        elif response.status_code==0 or response.status_code>=500:
            self.policy.cooldown(self.settings['failure_cooldown_sec'])

    def _resource_gate(self) -> tuple[bool, str | None, dict[str, Any]]:
        health=self._health()
        allowed,reason=self.policy.decide(health,self.recovery_config)
        if allowed and not self.byte_budget.ready(self.recovery_config.max_request_bytes+1024):
            allowed=False;reason='archive_budget_wait'
        self._last_health=health
        health["recovery_policy"]={"mode":self.policy.mode,"interval_sec":self.policy.delay,
            "cooldown_remaining_sec":round(max(0,self.policy.cooldown_until-time.monotonic()),2),
            "max_live_count":self.budget_settings['hard_live_count'],"max_live_age_sec":self.budget_settings['hard_live_age_sec'],
            "soft_live_count":self.budget_settings['soft_live_count'],"soft_live_age_sec":self.budget_settings['soft_live_age_sec']}
        if allowed:self.counters.bounded_opportunity_count+=1
        elif reason=='transport_paused':self.counters.pause_transport_count+=1
        elif reason=='memory_pressure':self.counters.pause_memory_count+=1
        elif reason=='load_pressure':self.counters.pause_load_count+=1
        elif reason in ('healthy_observation_wait','backend_cooldown','archive_budget_wait','archive_transport_cooldown','live_transport_cooldown','live_pressure_cooldown'):self.counters.pause_opportunity_count+=1
        else:self.counters.pause_live_count+=1
        return allowed,reason,health

    def _next_archive(self) -> Path | None:
        if self._session_store is not None:
            if time.monotonic() < self._session_until and self._session_path.is_file():
                return self._session_path
            self._close_session()
        root = Path(self.recovery_config.archive_dir)
        if not root.exists():
            return None
        candidates = sorted(
            [p for p in root.glob(self.recovery_config.archive_glob) if p.is_file()],
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        for path in candidates:
            marker = self._read_replayed_marker(path)
            signature = self._archive_signature(path)
            if marker and marker.get("signature") == signature:
                continue
            try:
                uri = f"file:{path}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as conn:
                    row = conn.execute(
                        """SELECT 1 FROM outbox_messages
                           WHERE state IN ('pending','retry','inflight') LIMIT 1"""
                    ).fetchone()
                    dead = None if row else conn.execute(
                        "SELECT COUNT(*) FROM outbox_messages WHERE state='dead'"
                    ).fetchone()
                remaining = int(row[0] or 0) if row else 0
                dead_count = int(dead[0] or 0) if dead else 0
            except Exception:
                continue
            if remaining > 0:
                try:
                    with closing(sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=1.0)) as due_db:
                        due = due_db.execute("""SELECT 1 FROM outbox_messages
                            WHERE state IN ('pending','retry','inflight') AND next_attempt_epoch<=? LIMIT 1""",(time.time(),)).fetchone()
                    if due:return path
                except sqlite3.Error:continue
                continue
            self._write_replayed_marker(path, dead_count=dead_count)
        return None

    def _marker_path(self, archive_path: Path) -> Path:
        return Path(str(archive_path) + self.recovery_config.marker_suffix)

    def _read_replayed_marker(self, archive_path: Path) -> dict[str, Any] | None:
        marker = self._marker_path(archive_path)
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def _archive_signature(self, archive_path: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for label, candidate in (
            ("db", archive_path),
            ("wal", Path(str(archive_path) + "-wal")),
            ("shm", Path(str(archive_path) + "-shm")),
        ):
            try:
                stat = candidate.stat()
                result[label] = {
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            except FileNotFoundError:
                result[label] = None
        return result

    def _write_replayed_marker(self, archive_path: Path, *, dead_count: int) -> None:
        if not archive_path.exists():
            return
        payload = {
            "archive_path": str(archive_path),
            "signature": self._archive_signature(archive_path),
            "dead_count": int(dead_count),
            "completed_utc": utc_now_iso(),
            "archive_retained": True,
        }
        safe_write_json(str(self._marker_path(archive_path)), payload)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.2, float(seconds)))
        except asyncio.TimeoutError:
            pass

    def status(self, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {
            "recovery_scheduler": "archive_throughput_v2_2",
            "archive_bandwidth_budget": self.byte_budget.status(),
            "archive_budget_settings": dict(self.budget_settings),
            "recent_archive_rejections": getattr(self, '_recent_archive_rejections', []),
            "transport_mode": "dedicated_archive_client",
            "auth_mode": "shared_token_broker",
            "max_archive_concurrency": 1,
            "adaptive_mode": self.policy.mode,
            "archive_interval_sec": self.policy.delay,
            "enabled": self.recovery_config.enabled,
            "running": self.running,
            "started_utc": self.started_utc,
            "archive_dir": self.recovery_config.archive_dir,
            "archive_glob": self.recovery_config.archive_glob,
            "archive_retention": "retain_with_replayed_marker",
            "batch_messages": self.recovery_config.max_messages_per_request,
            "batch_bytes": self.recovery_config.max_request_bytes,
            "counters": self.counters.__dict__.copy(),
            "status_generated_utc": utc_now_iso(),
        }
        if extra:
            result.update(extra)
        return result

    def _write_status(self, *, extra: dict[str, Any] | None = None) -> None:
        if extra is None and hasattr(self,"_last_health"):
            extra={"resource_gate":self._last_health}
        try:
            safe_write_json(self.recovery_config.status_file, self.status(extra=extra))
        except Exception as exc:
            log.warning("failed to write overflow recovery status: %s", exc)


def _mem_available_mb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0
