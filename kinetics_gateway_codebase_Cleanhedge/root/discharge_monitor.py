import json, sys, urllib.request
from datetime import datetime

request = urllib.request.Request(
    "http://127.0.0.1:8000/api/telemetry/compact",
    headers={"Authorization": "Bearer " + sys.argv[1]},
)

with urllib.request.urlopen(request, timeout=15) as response:
    data = json.load(response)

def v(asset, key):
    return ((asset.get("telemetry") or {}).get(key) or {}).get("v")

print(f"ALL-PAIR DISCHARGE MONITOR                     {datetime.now():%H:%M:%S}")
print("-" * 100)

for name, asset in sorted((data.get("pcs_devices") or {}).items()):
    print(
        f"{name.upper()} | SET={v(asset,'active_power_setpoint')}kW | "
        f"ACT={v(asset,'grid_active_power')}kW | "
        f"DC={v(asset,'dc_bus_voltage')}V | "
        f"BAT={v(asset,'battery_voltage')}V | "
        f"PCS={v(asset,'operating_state')}"
    )

solar = data.get("solar") or {}
print("-" * 100)
print(
    f"SOLAR={v(solar,'solar_active_power_kw')}kW | "
    f"EXPORT={v(solar,'pcc_export_power_kw')}kW | "
    f"IMPORT={v(solar,'grid_import_power_kw')}kW | "
    f"LOAD={v(solar,'site_load_power_kw')}kW"
)
