#!/usr/bin/env python3
"""Build the immutable CleanEdge S2 runtime manifest from the approved workbook.

Only rows proven available in the deployed gateway are emitted. Planned mappings,
driver extensions, control registers and explicit NOT_UPLOADED rows remain out of
the runtime manifest and are summarized in the adjacent coverage report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

SUBSTREAMS = [
    "ems_system", "pcs_extended", "bms_extended", "io_module",
    "liquid_cooling", "fire_protection", "dehumidifier", "remote_control",
    "bess_aux_consumption_meter", "combiner_box_meter", "solar_inverter",
]

SHEET_TO_SUBSTREAM = {
    "S2.1 EMS System": "ems_system", "S2.2 PCS Extended": "pcs_extended",
    "S2.3 BMS Extended": "bms_extended", "S2.4 IO Module": "io_module",
    "S2.5 Liquid Cooling": "liquid_cooling", "S2.6 Fire Protection": "fire_protection",
    "S2.7 Dehumidifier": "dehumidifier", "S2.8 Remote Control": "remote_control",
    "S2.9 BESS Aux Meter": "bess_aux_consumption_meter",
    "S2.10 Combiner Box Meter": "combiner_box_meter",
    "S2.11 Solar Inverter": "solar_inverter",
}

# Workbook source identity -> deployed telemetry-cache key. These are aliases,
# not additional device reads. The values come from SolarDriver.read_all().
SOLAR_RUNTIME_KEYS = {
    "on_grid_active_power": "pcc_export_power_kw",
    "grid_pickup_active_power": "grid_import_power_kw",
    "active_power_consumed_by_load": "site_load_power_kw",
    "total_feed_in_energy": "total_export_energy_kwh",
    "total_purchased_energy_from_grid": "total_grid_import_energy_kwh",
    "total_energy_consumed_by_load": "total_site_load_energy_kwh",
    "meter_power_factor": "meter_power_factor",
    "meter_ct_transformation_ratio": "meter_ct_ratio",
    "zero_export_control_status": "zero_export_control_status",
    "meter_communication_status": "meter_communication_status",
    "meter_a_phase_active_power": "meter_phase_a_power_kw",
    "meter_b_phase_active_power": "meter_phase_b_power_kw",
    "meter_c_phase_active_power": "meter_phase_c_power_kw",
    "meter_pt_ratio": "meter_pt_ratio",
    "online": "inverters_online", "offline": "inverters_offline",
    "on_grid": "inverters_on_grid", "fault": "inverters_faulted",
    "ac_active_power": "solar_active_power_kw",
    "ac_reactive_power": "solar_reactive_power_kvar",
    "daily_yield": "daily_yield", "total_yield": "total_yield_kwh",
    "actual_active_regulation": "actual_active_regulation_kw",
    "reactive_kvar_regulation": "reactive_regulation_kvar",
    "rated_power": "rated_power_kw",
    "operation_status_of_the_square_array": "array_operation_status",
    "inverter_array_operating_state_on_off": "array_running",
    "inverter_array_shutdown_state_on_off": "array_shutdown",
    "inverter_array_fault_state_or": "array_fault_or",
    "inverter_array_fault_state_and": "array_fault_and",
}

METER_PATH_KEYS = {
    "grid_average_current_a", "grid_frequency_hz", "pcc_export_power_kw",
    "grid_import_power_kw", "site_load_power_kw", "grid_reactive_power_kvar",
    "grid_apparent_power_kva", "meter_power_factor", "grid_line_voltage_v",
    "grid_phase_voltage_v", "total_export_energy_kwh",
    "total_grid_import_energy_kwh", "meter_communication_status", "grid_meter_source",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_catalogs(root: Path) -> tuple[dict[str, dict], dict[tuple[str, str, str], str]]:
    answer: dict[str, dict] = {}
    runtime_ids: dict[tuple[str, str, str], str] = {}
    for filename, vendor in (("bms_catalog.json", "kinetics_bms"), ("pcs_catalog.json", "kinetics_pcs")):
        payload = json.loads((root / "generated_protocols" / filename).read_text())
        for point in payload["points"]:
            item = dict(point)
            item["vendor"] = vendor
            answer[str(point["id"])] = item
            runtime_ids[(vendor, str(point["scope"]), str(point["key"]))] = str(point["id"])
    return answer, runtime_ids


def retention(value: object) -> str | None:
    text = str(value or "").upper().replace(" ", "")
    if "3Y" in text or "3_YEAR" in text:
        return "T3_3Y"
    if "1Y" in text or "1_YEAR" in text:
        return "T2_1Y"
    if "90D" in text or "90_DAY" in text:
        return "T1_90D"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--backend-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    args = parser.parse_args()

    catalogs, runtime_ids = load_catalogs(args.backend_root)
    workbook = load_workbook(args.workbook, data_only=True, read_only=False)
    points: list[dict] = []
    gaps: list[dict] = []
    counts: Counter[str] = Counter()
    seen: set[tuple] = set()

    for sheet, substream in SHEET_TO_SUBSTREAM.items():
        ws = workbook[sheet]
        for row in range(15, ws.max_row + 1):
            point_id = ws.cell(row, 1).value
            if not point_id:
                continue
            mode = str(ws.cell(row, 7).value or "")
            status = str(ws.cell(row, 29).value or "")
            source_id = str(ws.cell(row, 23).value or "")
            runtime_x = int(ws.cell(row, 6).value or 0)
            interval = ws.cell(row, 8).value
            if mode not in {"PERIODIC", "ON_CHANGE_HEARTBEAT"}:
                counts["explicitly_excluded"] += 1
                continue
            if status not in {"AVAILABLE_NOW", "MAPPED_EQUIVALENT"} or runtime_x < 1:
                counts["not_runtime_ready"] += 1
                gaps.append({"sheet": sheet, "row": row, "point_id": point_id,
                             "deployment_status": status, "reason": "not_runtime_ready"})
                continue

            element_index = None
            catalog = catalogs.get(source_id)
            if catalog:
                vendor, scope, key = catalog["vendor"], str(catalog["scope"]), str(catalog["key"])
            else:
                array = re.fullmatch(r"rack\.detail\.(vcell|tcell)\[(\d+)\]", source_id)
                if array:
                    vendor, scope, key = "kinetics_bms", "rack", array.group(1)
                    element_index = int(array.group(2))
                elif sheet == "S2.10 Combiner Box Meter":
                    json_path = str(ws.cell(row, 26).value or "")
                    path_keys = [x.strip() for x in json_path.split("/")]
                    key = next((x for x in path_keys if x in METER_PATH_KEYS), "")
                    if not key:
                        gaps.append({"sheet": sheet, "row": row, "point_id": point_id,
                                     "deployment_status": status, "reason": "unresolved_meter_cache_key"})
                        counts["unresolved"] += 1
                        continue
                    vendor, scope = "cleanedge_solar", "solar"
                elif sheet == "S2.11 Solar Inverter" and source_id in SOLAR_RUNTIME_KEYS:
                    # Prefix aliases to prevent S2.10/S2.11 identity collisions.
                    vendor, scope, key = "cleanedge_solar", "solar", f"hopecombox__{source_id}"
                else:
                    gaps.append({"sheet": sheet, "row": row, "point_id": point_id,
                                 "deployment_status": status, "reason": "no_deployed_source_mapping"})
                    counts["unresolved"] += 1
                    continue

            identity = (vendor, scope, key, element_index)
            if identity in seen:
                gaps.append({"sheet": sheet, "row": row, "point_id": point_id,
                             "deployment_status": status, "reason": "duplicate_runtime_identity"})
                counts["duplicate"] += 1
                continue
            seen.add(identity)
            item = {
                "vendor": vendor, "scope": scope, "key": key,
                "point_id": str(point_id), "substream": substream, "mode": mode,
                "interval_sec": float(interval), "expected_instances": runtime_x,
            }
            keep = retention(ws.cell(row, 27).value)
            if keep:
                item["retention_class"] = keep
            if element_index is not None:
                item["element_index"] = element_index
            if key.startswith("hopecombox__"):
                item["runtime_source_key"] = SOLAR_RUNTIME_KEYS[source_id]
            if catalog:
                item["source_point_id"] = runtime_ids[(vendor, scope, key)]
            points.append(item)
            counts["emitted"] += 1

    payload = {
        "policy_version": "cleanedge-s2-frozen-v1.0",
        "contract_sha256": sha256(args.workbook),
        "substreams": SUBSTREAMS,
        "expected_canonical_points": len(points),
        "expected_runtime_signals": sum(int(p["expected_instances"]) for p in points),
        "points": points,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    report = {
        "workbook": args.workbook.name, "workbook_sha256": sha256(args.workbook),
        "manifest": args.output.name, "manifest_sha256": sha256(args.output),
        "counts": dict(counts), "runtime_points": len(points),
        "runtime_signals": payload["expected_runtime_signals"], "gaps": gaps,
    }
    args.coverage.parent.mkdir(parents=True, exist_ok=True)
    args.coverage.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("manifest_sha256", "counts", "runtime_points", "runtime_signals")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
