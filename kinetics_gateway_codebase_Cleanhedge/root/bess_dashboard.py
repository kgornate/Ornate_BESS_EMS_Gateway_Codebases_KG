import json, sys, urllib.request
from datetime import datetime

token = sys.argv[1]

def get(path):
    request = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        headers={"Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)

def n(value):
    try:
        return f"{float(value):.1f}"
    except:
        return "--"

print(f"BESS / SOLAR LIVE DASHBOARD                    {datetime.now():%H:%M:%S}")
print("-" * 100)

try:
    data = get("/api/control-sequence/status/all/compact")
    pairs = data.get("by_pair_id", {})

    for pair_id in sorted(pairs):
        item = pairs[pair_id]
        runtime = item.get("runtime") or {}
        summary = item.get("summary") or {}

        print(
            f"{pair_id.upper()} | "
            f"SET={n(summary.get('pcs_power_setpoint_kw'))}kW  "
            f"ACT={n(summary.get('pcs_actual_power_kw'))}kW  | "
            f"DC={n(summary.get('pcs_dc_bus_voltage_v'))}V  "
            f"BAT={n(summary.get('pcs_battery_voltage_v'))}V  | "
            f"PRE={summary.get('precharge_state')}  "
            f"CONT={summary.get('contactors_ready')}  "
            f"PCS={summary.get('pcs_operating_state')}  | "
            f"{runtime.get('run_status')}"
        )
except Exception:
    print("PAIR STATUS TEMPORARILY UNAVAILABLE")

print("-" * 100)

try:
    data = get("/api/telemetry/compact")
    telemetry = data["solar"]["telemetry"]
    value = lambda key: (telemetry.get(key) or {}).get("v")

    print(
        f"SOLAR={n(value('solar_active_power_kw'))}kW  | "
        f"EXPORT={n(value('pcc_export_power_kw'))}kW  | "
        f"IMPORT={n(value('grid_import_power_kw'))}kW  | "
        f"LOAD={n(value('site_load_power_kw'))}kW"
    )
    print(
        f"GRID={n(value('grid_frequency_hz'))}Hz  | "
        f"LINE={n(value('grid_line_voltage_v'))}V"
    )
except Exception:
    print("SOLAR / GRID TEMPORARILY UNAVAILABLE")
