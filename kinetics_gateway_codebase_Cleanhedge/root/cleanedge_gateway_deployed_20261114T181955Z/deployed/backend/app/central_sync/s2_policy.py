"""Frozen site-specific S2 recording manifest. No Modbus/polling/control changes."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .producer_common import fingerprint

SUPPORTED_MODES = frozenset({"PERIODIC", "ON_CHANGE_HEARTBEAT"})
SUBSTREAMS = (
    "ems_system", "pcs_extended", "bms_extended", "io_module", "liquid_cooling",
    "fire_protection", "dehumidifier", "remote_control", "bess_aux_consumption_meter", "combiner_box_meter",
    "solar_inverter",
)

class S2PolicyError(ValueError):
    """Invalid or incomplete frozen S2 policy; never silently fall back."""

@dataclass(frozen=True)
class S2RecordingPolicy:
    point_id: str
    vendor: str
    scope: str
    key: str
    substream: str
    mode: str
    interval_sec: float
    revision: str
    retention_class: str | None = None
    element_index: int | None = None
    source_point_id: str | None = None


class S2PolicyManifest:
    def __init__(self, path: str | Path, *, expected_sha256: str | None = None):
        path = Path(path)
        raw = path.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise S2PolicyError("frozen S2 manifest checksum mismatch; refuse to run unapproved policies")
        data = json.loads(raw.decode("utf-8"))
        if data.get("policy_version") != "cleanedge-s2-frozen-v1.0":
            raise S2PolicyError("unexpected CleanEdge S2 policy version")
        expected_substreams = list(SUBSTREAMS)
        if data.get("substreams") != expected_substreams:
            raise S2PolicyError("frozen S2 substreams differ from backend contract")
        points = data.get("points")
        canonical_count = int(data.get("expected_canonical_points") or 0)
        runtime_count = int(data.get("expected_runtime_signals") or 0)
        if not isinstance(points, list) or not canonical_count or len(points) != canonical_count:
            raise S2PolicyError("frozen CleanEdge manifest point count mismatch")
        self.version = data["policy_version"]
        self.source_sha256 = str(data.get("contract_sha256") or "")
        self.by_key: dict[tuple[str, str, str], list[S2RecordingPolicy]] = {}
        total_instances = 0
        for i, p in enumerate(points):
            try:
                vendor, scope, key = (str(p[n]) for n in ("vendor", "scope", "key"))
                substream, mode = str(p["substream"]), str(p["mode"])
                interval = float(p["interval_sec"])
                point_id = str(p["point_id"])
                multiplier = int(p["expected_instances"])
                element_index = p.get("element_index")
                element_index = None if element_index is None else int(element_index)
                source_point_id = None if p.get("source_point_id") is None else str(p["source_point_id"])
            except (KeyError, TypeError, ValueError) as e:
                raise S2PolicyError(f"invalid policy at row {i}") from e
            if vendor not in {"kinetics_bms", "kinetics_pcs", "cleanedge_solar", "cleanedge_meter"} or scope not in {"bank", "rack", "pcs", "environment", "solar", "meter"}:
                raise S2PolicyError(f"unexpected vendor or scope: {vendor}/{scope}")
            if mode not in SUPPORTED_MODES or substream not in expected_substreams:
                raise S2PolicyError(f"invalid recording mode/substream for {point_id}")
            if not (0 < interval <= 86400) or not (1 <= multiplier <= 4):
                raise S2PolicyError(f"invalid interval or instance count for {point_id}")
            if element_index is not None and element_index < 0:
                raise S2PolicyError(f"invalid element index for {point_id}")
            # Canonical IDs are backend contracts and may retain established
            # prefixes (for example hopecombox.* or mfm.*) that differ from the
            # deployed runtime scope. Never rewrite them merely to satisfy a
            # gateway-local naming convention.
            if not key or not point_id or not self.source_sha256:
                raise S2PolicyError(f"invalid point identity or provenance for {point_id}")
            ident = (vendor, scope, key)
            revision = fingerprint([self.version, vendor, scope, key, element_index, substream, mode, interval])
            retention = p.get("retention_class")
            if retention is not None and retention not in {"T1_90D", "T2_1Y", "T3_3Y"}:
                raise S2PolicyError(f"invalid retention class for {point_id}")
            policies = self.by_key.setdefault(ident, [])
            if any(x.element_index == element_index for x in policies):
                raise S2PolicyError(f"duplicate policy: {ident} element={element_index}")
            policies.append(S2RecordingPolicy(point_id, vendor, scope, key, substream, mode, interval, revision, retention, element_index, source_point_id))
            total_instances += multiplier
        if total_instances != runtime_count:
            raise S2PolicyError("frozen CleanEdge runtime count mismatch")

    def resolve_all(self, vendor: str, scope: str, key: str, substream: str, catalog_point: dict[str, Any] | None) -> list[S2RecordingPolicy]:
        policies = self.by_key.get((vendor, scope, key))
        if not policies:
            raise S2PolicyError(f"missing frozen S2 policy: {vendor}/{scope}/{key} (substream {substream})")
        if any(p.substream != substream for p in policies):
            raise S2PolicyError(f"S2 substream drift: {vendor}/{scope}/{key}")
        # Expanded array policies intentionally use canonical point IDs that differ
        # from the one block-register catalog ID. Scalar policies must still match.
        if catalog_point is None and vendor not in {"cleanedge_solar", "cleanedge_meter"}:
            raise S2PolicyError(f"S2 catalog missing for {vendor}/{scope}/{key}")
        if catalog_point is not None and any(
            p.element_index is None and str(catalog_point.get("id")) != (p.source_point_id or p.point_id)
            for p in policies
        ):
            raise S2PolicyError(f"S2 catalog drift for {vendor}/{scope}/{key}")
        return policies

    def resolve(self, vendor: str, scope: str, key: str, substream: str, catalog_point: dict[str, Any] | None) -> S2RecordingPolicy:
        policies = self.resolve_all(vendor, scope, key, substream, catalog_point)
        if len(policies) != 1:
            raise S2PolicyError(f"expanded S2 point requires resolve_all: {vendor}/{scope}/{key}")
        return policies[0]

