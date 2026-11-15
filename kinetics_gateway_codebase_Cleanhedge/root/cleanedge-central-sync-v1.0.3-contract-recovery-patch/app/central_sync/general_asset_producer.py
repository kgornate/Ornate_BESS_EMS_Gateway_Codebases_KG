from __future__ import annotations

import asyncio
import json
import math
import struct
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import CentralSyncConfig
from .contracts import utc_now_iso
from .local_gateway_api import LocalGatewayApiClient
from .outbox import OutboxCapacityError, OutboxStore
from .producer_common import ProducerCounters, ProducerLoopMixin, emit_message, fingerprint
from .s2_policy import S2PolicyManifest, SUBSTREAMS
from .s2_chunks import split_s2_records

S2_SUBSTREAMS = SUBSTREAMS

_PERMILLE_PERCENT_KEYS = {"soc", "soh", "rack_inner_soc"}


def normalize_runtime_point(asset: dict[str, Any], key: str, point: dict[str, Any]) -> dict[str, Any]:
    """Return an upload view in canonical engineering units.

    The runtime cache intentionally retains vendor units/raw words.  Central
    Sync converts only unit-qualified fields whose canonical meaning is known.
    """
    normalized = dict(point)
    value = normalized.get("value")
    unit = str(normalized.get("unit") or "")
    if key in _PERMILLE_PERCENT_KEYS and unit == "‰":
        try:
            normalized["value"] = float(value) / 10.0
            normalized["unit"] = "%"
        except (TypeError, ValueError):
            pass
    elif str(asset.get("asset_type") or "").lower() == "energy_meter" and key.startswith("essmeter_"):
        # The CleanEdge cache currently exposes the Acrel IEEE-754 bit pattern
        # as an unsigned integer.  Decode without adding any southbound read.
        raw = normalized.get("raw", value)
        try:
            raw_int = int(raw)
            decoded = struct.unpack(">f", struct.pack(">I", raw_int & 0xFFFFFFFF))[0]
            if math.isfinite(decoded):
                normalized["value"] = float(decoded)
        except (TypeError, ValueError, struct.error, OverflowError):
            pass
    return normalized

# These raw points are represented in S1 through the normalized fast-pair view.
S1_CRITICAL_KEYS = {
    # Lineage rack/system
    "system_voltage", "system_current", "system_soc", "system_soh", "system_max_charge_power",
    "system_max_discharge_power", "system_max_charge_current", "system_max_discharge_current",
    "rack_voltage", "rack_current", "rack_soc", "rack_soh", "rack_max_charge_power",
    "rack_max_discharge_power", "rack_max_charge_current", "rack_max_discharge_current",
    "positive_contactor_feedback", "negative_contactor_feedback", "positive_insulation_resistance",
    "negative_insulation_resistance", "rack_max_cell_voltage", "rack_min_cell_voltage",
    "rack_max_cell_temperature", "rack_min_cell_temperature", "system_state", "rack_state",
    # Elecod
    "status_word", "total_active_power", "total_reactive_power", "total_power_factor",
    "dc_voltage", "dc_current", "dc_power", "dc_bus_voltage", "grid_frequency_a",
    "grid_voltage_ab", "grid_voltage_bc", "grid_voltage_ca",
}


def _load_points(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("points", [])
    return [dict(x) for x in payload if isinstance(x, dict)]


class CatalogIndex:
    def __init__(self, root: str | Path) -> None:
        root = Path(root)
        self.by_vendor_scope_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for vendor, name in (
            ("acrel", "acrel_combiner_meter_catalog.json"),
            ("lineage", "lineage_bms_catalog.json"),
            ("elecod", "elecod_pcs_catalog.json"),
            ("kinetics_bms", "bms_catalog.json"),
            ("kinetics_pcs", "pcs_catalog.json"),
        ):
            for point in _load_points(root / name):
                key = str(point.get("key") or "")
                scope = str(point.get("scope") or "")
                if key and scope:
                    self.by_vendor_scope_key[(vendor, scope, key)] = point

    def get(self, vendor: str, scope: str, key: str) -> dict[str, Any] | None:
        return self.by_vendor_scope_key.get((vendor, scope, key))


def _vendor_tag(asset: dict[str, Any], vendors: dict[str, Any]) -> str:
    asset_type = str(asset.get("asset_type") or "").lower()
    if asset_type == "energy_meter" and asset.get("vendor") == "acrel":
        return "acrel"
    aid = str(asset.get("asset_id") or "").lower()
    if aid == "solar_plant" or asset_type == "solar_plant":
        return "cleanedge_solar"
    if "pcs" in asset_type or aid.startswith("pcs_"):
        v = str(vendors.get("pcs") or "kinetics").lower()
        return "elecod" if v == "elecod" else "kinetics_pcs"
    v = str(vendors.get("bms") or "kinetics").lower()
    return "lineage" if v == "lineage" else "kinetics_bms"


def _scope(asset: dict[str, Any]) -> str:
    aid = str(asset.get("asset_id") or "").lower()
    asset_type = str(asset.get("asset_type") or "").lower()
    if aid == "solar_plant" or asset_type == "solar_plant":
        return "solar"
    if asset_type == "energy_meter" and asset.get("vendor") == "acrel":
        return "meter"
    if aid.startswith("pcs_") or "pcs" in asset_type:
        return "pcs"
    # CleanEdge's aggregate bank object also carries rack_id=1.  Asset identity
    # is authoritative; otherwise all canonical bank/EMS points are wrongly
    # treated as rack points.
    if aid.startswith("bms_bank") or asset_type in {"bank", "bams", "system"}:
        return "bank"
    if aid.startswith("bms_rack_") or asset.get("rack_id") is not None:
        return "rack"
    return "environment"


def _is_writable(point: dict[str, Any]) -> bool:
    access = str(point.get("access") or point.get("rw") or "R").upper()
    return "W" in access


def classify_substream(asset: dict[str, Any], key: str, point: dict[str, Any] | None) -> str:
    p = point or {}
    scope = _scope(asset)
    if scope == "solar":
        # CleanEdge's frozen contract assigns every HopeComBox/string-inverter
        # point to S2.11.  Some of those points use grid_* names, but their
        # source identity is still the solar plant and must remain authoritative.
        return "solar_inverter"
    if scope == "meter":
        # The separately identified Acrel/ABB MFM is the S2.10 source.
        return "combiner_box_meter"
    category = str(p.get("category") or "").lower()
    text = " ".join([key.lower(), category, str(p.get("name_en") or "").lower()])

    if _is_writable(p) or category in {"control", "control_parameter", "control_parameter_2", "remote_control", "setting", "communication_parameter", "rtc"}:
        return "remote_control"
    if category == "chiller" or "chiller" in text or "liquid cooling" in text:
        return "liquid_cooling"
    if category == "dehumidifier" or "dehumid" in text:
        return "dehumidifier"
    if any(token in text for token in ("fire_start", "fire fault", "fire_fault", "aerosol", "combustible_gas", "gas alarm", "emergency_stop")):
        return "fire_protection"
    if category in {"temp_humidity", "water_sensor"}:
        return "io_module"
    if scope == "environment":
        return "io_module"
    if scope == "pcs":
        return "pcs_extended"
    if scope == "bank" and category in {"system_data", "general", "status"}:
        return "ems_system"
    if scope == "bank" and category == "system_alarm":
        return "io_module"
    return "bms_extended"


def policy_for(point: dict[str, Any] | None) -> tuple[str, float, float, str, str, str]:
    p = point or {}
    poll_class = str(p.get("poll_class") or "normal").lower()
    category = str(p.get("category") or "").lower()
    writable = _is_writable(p)
    if writable or poll_class == "fast" or category in {"status", "alarm", "system_alarm", "rack_alarm"}:
        return "ON_CHANGE_HEARTBEAT", 30.0, 5.0, "High", "T2_1Y", "change_or_heartbeat"
    if poll_class == "bulk":
        return "BULK_30S", 30.0, 30.0, "Normal", "T1_90D", "periodic"
    if poll_class == "slow":
        return "SLOW_60S", 60.0, 60.0, "Normal", "T2_1Y", "periodic"
    return "NORMAL_5S", 5.0, 5.0, "Normal", "T1_90D", "periodic"


class GeneralAssetProducer(ProducerLoopMixin):
    """S2 producer emitting Northbound-compatible point records."""

    def __init__(self, *, config: CentralSyncConfig, outbox: OutboxStore, local_api: Any | None = None, catalog: CatalogIndex | None = None) -> None:
        self.config = config
        self.producer_config = config.general_assets
        self.outbox = outbox
        self.local_api = local_api or LocalGatewayApiClient(config.local_gateway_api)
        self._owns_api = local_api is None
        self.catalog = catalog or CatalogIndex(self.producer_config.catalog_dir)
        self.counters = ProducerCounters()
        self.running = False
        self.started_utc = utc_now_iso()
        self._stop = asyncio.Event()
        self._last_emit: dict[str, float] = {}
        self._last_fingerprint: dict[str, str] = {}
        # Explicit opt-in prevents any new behavior in an unchanged installation.
        if self.producer_config.s2_policy_manifest_path and not self.producer_config.s2_policy_expected_sha256:
            raise ValueError("optimized S2 requires an approved s2_policy_expected_sha256 in config")
        self.s2_policy = (
            S2PolicyManifest(self.producer_config.s2_policy_manifest_path,
                             expected_sha256=self.producer_config.s2_policy_expected_sha256)
            if self.producer_config.s2_policy_manifest_path else None
        )
        self._s2_checkpoints: dict[str, tuple[float, str, str]] = (
            outbox.s2_load_checkpoints(self.producer_config.stream) if self.s2_policy else {}
        )

    async def close(self) -> None:
        if self._owns_api:
            await self.local_api.close()

    async def collect_once(self) -> dict[str, Any]:
        now_mono = time.monotonic()
        now_epoch = time.time()
        self.counters.poll_count += 1
        self.counters.last_poll_utc = utc_now_iso()
        snapshot = await self.local_api.telemetry_snapshot()
        normalized = snapshot.get("normalized") if isinstance(snapshot.get("normalized"), dict) else {}
        vendors = normalized.get("vendors") if isinstance(normalized, dict) else {}
        sampled_utc = str(snapshot.get("timestamp") or utc_now_iso())

        due: dict[str, list[dict[str, Any]]] = {s: [] for s in S2_SUBSTREAMS}
        pending_checkpoints: dict[str, dict[str, tuple[float, str, str]]] = {s: {} for s in S2_SUBSTREAMS}
        for asset in self._assets(snapshot):
            if asset.get("asset_type") == "energy_meter" and (
                not self.s2_policy or self.s2_policy.version != "cleanedge-s2-frozen-v1.0"
                or asset.get("disabled")
            ):
                continue
            vendor = _vendor_tag(asset, vendors or {})
            scope = _scope(asset)
            source_id = f"{vendor}_source"
            runtime_asset_id = str(asset.get("asset_id") or "unknown")
            for key, element_index, raw_point in self._expanded_telemetry(asset, vendor, scope):
                if not isinstance(raw_point, dict):
                    raw_point = {"value": raw_point, "quality": "unknown"}
                raw_point = normalize_runtime_point(asset, str(key), raw_point)
                # A frozen manifest is an allow-list. Runtime/catalog additions
                # are not uploaded until the approved workbook is regenerated.
                if self.s2_policy and (vendor, scope, str(key)) not in self.s2_policy.by_key:
                    continue
                point = self.catalog.get(vendor, scope, str(key))
                if point and point.get("poll_enabled") is False:
                    continue
                if str(key) in S1_CRITICAL_KEYS:
                    # In frozen mode the approved workbook—not a legacy name
                    # list—decides whether an S1-adjacent point also belongs in
                    # S2. Explicit DUPLICATE_S1 rows never enter the manifest.
                    approved_for_s2 = (
                        self.s2_policy is not None
                        and (vendor, scope, str(key)) in self.s2_policy.by_key
                    )
                    if not approved_for_s2:
                        continue
                # The approved workbook is authoritative for frozen S2 routing.
                # Heuristic classification remains only for legacy/unfrozen mode.
                if self.s2_policy:
                    approved = self.s2_policy.by_key[(vendor, scope, str(key))]
                    substream = approved[0].substream
                else:
                    substream = classify_substream(asset, str(key), point)
                if substream not in due:
                    continue
                policy, record_sec, upload_sec, signal_priority, retention, mode = policy_for(point)
                signal_suffix = str(key) if element_index is None else f"{key}[{element_index}]"
                runtime_signal_id = f"{source_id}.{runtime_asset_id}.{signal_suffix}"
                fp = fingerprint([raw_point.get("value"), raw_point.get("quality")])
                if self.s2_policy:
                    frozen_options = self.s2_policy.resolve_all(vendor, scope, str(key), substream, point)
                    frozen = next((p for p in frozen_options if p.element_index == element_index), None)
                    if frozen is None:
                        continue
                    policy, record_sec = frozen.mode, frozen.interval_sec
                    if frozen.retention_class is not None:
                        retention = frozen.retention_class
                    mode = "change_or_heartbeat" if policy == "ON_CHANGE_HEARTBEAT" else "periodic"
                    previous = self._s2_checkpoints.get(runtime_signal_id)
                    if previous is None:
                        reason = "startup"
                    elif previous[2] != frozen.revision:
                        reason = "policy_change"
                    elif now_epoch < previous[0]:
                        reason = "clock_reset"
                    elif mode == "change_or_heartbeat" and fp != previous[1]:
                        reason = "change"
                    elif now_epoch - previous[0] >= record_sec:
                        reason = "heartbeat" if mode == "change_or_heartbeat" else "periodic"
                    else:
                        continue
                    pending_checkpoints[substream][runtime_signal_id] = (now_epoch, fp, frozen.revision)
                else:
                    # Legacy behavior is left intact until the frozen manifest is opted in.
                    last_t = self._last_emit.get(runtime_signal_id)
                    last_fp = self._last_fingerprint.get(runtime_signal_id)
                    if last_t is None:
                        reason = "startup"
                    elif mode == "change_or_heartbeat" and fp != last_fp:
                        reason = "change"
                    elif now_mono - last_t >= record_sec:
                        reason = "heartbeat" if mode == "change_or_heartbeat" else "periodic"
                    else:
                        continue

                point_id = frozen.point_id if self.s2_policy else str((point or {}).get("id") or f"{vendor}.{scope}.{key}")
                display_name = (point or {}).get("name_en") or raw_point.get("name") or str(key).replace("_", " ").title()
                category = (point or {}).get("category") or raw_point.get("category")
                unit = (
                    raw_point.get("unit")
                    if str(key) in _PERMILLE_PERCENT_KEYS and raw_point.get("unit") == "%"
                    else (point or {}).get("unit") or raw_point.get("unit")
                )
                access = str((point or {}).get("access") or "R")
                record = {
                    "record_type": "general_asset_signal",
                    "sample_source": "gateway_runtime_cache",
                    "persisted_source": False,
                    "observed_at_utc": sampled_utc,
                    "source_updated_utc": asset.get("timestamp") or sampled_utc,
                    "snapshot_age_sec": None,
                    "source_data_age_ms": None,
                    "source_id": source_id,
                    "runtime_asset_id": runtime_asset_id,
                    "base_asset_id": str(asset.get("asset_type") or scope),
                    "substream": substream,
                    "runtime_signal_id": runtime_signal_id,
                    "point_id": point_id,
                    "signal": signal_suffix,
                    "display_name": display_name,
                    "value": raw_point.get("value"),
                    "unit": unit,
                    "category": category,
                    "quality": raw_point.get("quality") or "unknown",
                    "policy": policy,
                    "central_record_sec": record_sec,
                    "upload_batch_sec": upload_sec,
                    "signal_priority": signal_priority,
                    "retention_class": retention,
                    "rw": access,
                    "key_signal": bool((point or {}).get("key_signal", False)),
                    "trigger_reason": reason,
                    # Additive dimensions; backend may ignore them.
                    "vendor": vendor,
                    "address_dec": (point or {}).get("address_dec") if not self.s2_policy else (point or {}).get("address_dec", (point or {}).get("address")),
                    "address_hex": (point or {}).get("address_hex") or (point or {}).get("address"),
                    "read_fc": (point or {}).get("read_fc") if not self.s2_policy else (point or {}).get("read_fc", (point or {}).get("read_function")),
                    "datatype": (point or {}).get("datatype") if not self.s2_policy else (point or {}).get("datatype", (point or {}).get("data_type")),
                }
                if element_index is not None:
                    record["element_index"] = element_index
                if vendor == "acrel":
                    # Use each block's real read time, not the newer asset time.
                    updated = raw_point.get("timestamp")
                    record["source_updated_utc"] = updated
                    try:
                        stamp = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                        if stamp.tzinfo is None:
                            raise ValueError("source timestamp must include timezone")
                        record["source_data_age_ms"] = max(0.0, (now_epoch - stamp.timestamp()) * 1000)
                    except (ValueError, TypeError, OverflowError):
                        record["source_data_age_ms"] = None
                    record["snapshot_age_sec"] = asset.get("age_seconds")
                    record["source_mode"] = asset.get("source")
                    record["scaling_validated"] = asset.get("scaling_validated", False)
                    record["meter_online"] = asset.get("online", False)
                    record["upload_batch_sec"] = 5.0
                    if not asset.get("online") and record["quality"] not in {"bad", "stale"}:
                        record["quality"] = "bad"
                due[substream].append(record)
                if not self.s2_policy:
                    self._last_emit[runtime_signal_id] = now_mono
                    self._last_fingerprint[runtime_signal_id] = fp

        messages = []
        for substream in S2_SUBSTREAMS:
            records = due[substream]
            if not records:
                continue
            try:
                if self.s2_policy:
                    chunks = split_s2_records(records, max_message_bytes=self.producer_config.s2_max_logical_message_bytes)
                    emitted = await asyncio.to_thread(
                        self.outbox.enqueue_s2_atomic,
                        config=self.config,
                        stream=self.producer_config.stream,
                        substream=substream,
                        priority=self.producer_config.priority,
                        chunks=chunks,
                        checkpoints=pending_checkpoints[substream],
                        created_at_utc=sampled_utc,
                        max_message_bytes=self.producer_config.s2_max_logical_message_bytes,
                    )
                    # Update the in-memory view only once SQLite COMMIT succeeds.
                    self._s2_checkpoints.update(pending_checkpoints[substream])
                else:
                    inserted, msg = await asyncio.to_thread(
                        emit_message,
                        config=self.config,
                        outbox=self.outbox,
                        stream=self.producer_config.stream,
                        substream=substream,
                        priority=self.producer_config.priority,
                        records=records,
                        created_at_utc=sampled_utc,
                    )
                    emitted = [msg] if inserted else []
            except OutboxCapacityError as exc:
                self.counters.failure_count += 1
                self.counters.last_error = str(exc)
                self._write_status()
                return {"emitted": bool(messages), "reason": "outbox_capacity", "messages": messages, "error": str(exc)}
            for msg in emitted:
                messages.append({"substream": substream, "message_id": msg.message_id,
                                 "sequence": msg.sequence, "record_count": len(msg.records)})
                self.counters.message_count += 1
                self.counters.record_count += len(msg.records)
                self.counters.last_message_id = msg.message_id
                self.counters.last_sequence = msg.sequence

        self.counters.success_count += 1
        self.counters.last_success_utc = utc_now_iso()
        if messages:
            self.counters.last_enqueue_utc = utc_now_iso()
        self.counters.last_error = None
        self._write_status()
        return {"emitted": bool(messages), "message_count": len(messages), "messages": messages, "record_count": sum(m["record_count"] for m in messages)}

    def _expanded_telemetry(self, asset: dict[str, Any], vendor: str, scope: str) -> Iterable[tuple[str, int | None, dict[str, Any]]]:
        """Expand only array elements explicitly selected by the frozen manifest."""
        telemetry = dict(asset.get("telemetry") or {})
        if vendor == "kinetics_pcs" and scope == "pcs":
            # Preserve the frozen canonical register keys while consuming the
            # gateway's clearer semantic aliases for these five addresses.
            for canonical_key, source_key in {
                "reg_1400": "remote_on_off_command",
                "reg_1402": "remote_local_mode",
                "reg_1406": "product_run_mode",
                "reg_1407": "pq_work_mode",
                "reg_1409": "active_power_setpoint",
            }.items():
                if canonical_key not in telemetry and source_key in telemetry:
                    telemetry[canonical_key] = telemetry[source_key]
        if vendor == "cleanedge_solar":
            # Workbook identities intentionally remain HopeComBox-facing while
            # values are read from the already-populated CleanEdge cache. This
            # creates no Modbus transaction and cannot compete with control I/O.
            for virtual_key, source_key in {
                "hopecombox__on_grid_active_power": "pcc_export_power_kw",
                "hopecombox__grid_pickup_active_power": "grid_import_power_kw",
                "hopecombox__active_power_consumed_by_load": "site_load_power_kw",
                "hopecombox__total_feed_in_energy": "total_export_energy_kwh",
                "hopecombox__total_purchased_energy_from_grid": "total_grid_import_energy_kwh",
                "hopecombox__total_energy_consumed_by_load": "total_site_load_energy_kwh",
                "hopecombox__meter_power_factor": "meter_power_factor",
                "hopecombox__meter_ct_transformation_ratio": "meter_ct_ratio",
                "hopecombox__zero_export_control_status": "zero_export_control_status",
                "hopecombox__meter_communication_status": "meter_communication_status",
                "hopecombox__meter_a_phase_active_power": "meter_phase_a_power_kw",
                "hopecombox__meter_b_phase_active_power": "meter_phase_b_power_kw",
                "hopecombox__meter_c_phase_active_power": "meter_phase_c_power_kw",
                "hopecombox__meter_pt_ratio": "meter_pt_ratio",
                "hopecombox__online": "inverters_online",
                "hopecombox__offline": "inverters_offline",
                "hopecombox__on_grid": "inverters_on_grid",
                "hopecombox__fault": "inverters_faulted",
                "hopecombox__ac_active_power": "solar_active_power_kw",
                "hopecombox__ac_reactive_power": "solar_reactive_power_kvar",
                "hopecombox__daily_yield": "daily_yield",
                "hopecombox__total_yield": "total_yield_kwh",
                "hopecombox__actual_active_regulation": "actual_active_regulation_kw",
                "hopecombox__reactive_kvar_regulation": "reactive_regulation_kvar",
                "hopecombox__rated_power": "rated_power_kw",
                "hopecombox__operation_status_of_the_square_array": "array_operation_status",
                "hopecombox__inverter_array_operating_state_on_off": "array_running",
                "hopecombox__inverter_array_shutdown_state_on_off": "array_shutdown",
                "hopecombox__inverter_array_fault_state_or": "array_fault_or",
                "hopecombox__inverter_array_fault_state_and": "array_fault_and",
            }.items():
                if source_key in telemetry:
                    telemetry[virtual_key] = telemetry[source_key]
        for key, raw in telemetry.items():
            point = raw if isinstance(raw, dict) else {"value": raw, "quality": "unknown"}
            policies = self.s2_policy.by_key.get((vendor, scope, str(key)), []) if self.s2_policy else []
            indexed = sorted({p.element_index for p in policies if p.element_index is not None})
            if not indexed:
                yield str(key), None, point
                continue
            values = point.get("value")
            if not isinstance(values, (list, tuple)):
                continue
            for index in indexed:
                if index is None or index >= len(values):
                    continue
                expanded = dict(point)
                expanded["value"] = values[index]
                expanded.pop("raw", None)
                yield str(key), index, expanded

    @staticmethod
    def _assets(snapshot: dict[str, Any]) -> Iterable[dict[str, Any]]:
        seen: set[str] = set()
        candidates: list[Any] = []
        candidates.append(snapshot.get("bank"))
        # The frozen contract defines one system/bank instance.  CleanEdge's
        # bms_banks entries mirror pair-specific views and must not multiply
        # canonical bank points; per-pair measurements come from rack assets.
        candidates.extend(snapshot.get("racks") or [])
        if isinstance(snapshot.get("meters"), dict):
            candidates.extend(snapshot["meters"].values())
        if isinstance(snapshot.get("environment"), dict):
            candidates.extend(snapshot["environment"].values())
        if isinstance(snapshot.get("pcs_devices"), dict):
            candidates.extend(snapshot["pcs_devices"].values())
        elif snapshot.get("pcs"):
            candidates.append(snapshot.get("pcs"))
        if isinstance(snapshot.get("solar"), dict):
            candidates.append(snapshot.get("solar"))
        for asset in candidates:
            if not isinstance(asset, dict):
                continue
            aid = str(asset.get("asset_id") or "")
            if aid and aid in seen:
                continue
            if aid:
                seen.add(aid)
            yield asset

