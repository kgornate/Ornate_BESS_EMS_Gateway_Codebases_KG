# Phase 3C - ABB grid import/export integration

This patch keeps the Phase 3B Hopewind integration as the source of solar
generation and adds the ABB ACDB MFM at `172.16.0.113:502`, Unit ID 1, as the
authoritative grid-flow meter.

## Public telemetry mapping

- `solar_active_power_kw`: Hopewind aggregate solar generation.
- `pcc_export_power_kw`: ABB positive active power.
- `grid_import_power_kw`: magnitude of ABB negative active power.
- `site_load_power_kw`: solar generation - grid export + grid import.
- `total_export_energy_kwh`: ABB register 40159.
- `total_grid_import_energy_kwh`: ABB register 40167 magnitude.
- Grid PF, reactive power, apparent power, voltage, current and frequency are
  exposed from the ABB meter.

The old Hopewind PCC fields remain visible with the
`hopewind_reported_` prefix for diagnostics only.

## Safety

- Both devices are read-only; no ABB write API exists.
- The scheduler must be inactive during installation and validation.
- `dispatch_validation_complete` remains false after installation.
- The patch does not start charging or discharging.
- Keep ModScan and other ABB Modbus clients disconnected because the logger
  permits only one active client.

## Install

```sh
cd /root/kinetics_phase3c_abb_grid_meter_20261105
CONFIRM_IDLE=YES bash install_phase3c_abb_grid_meter.sh
```

## Verify

```sh
bash /root/kinetics_phase3c_abb_grid_meter_20261105/verify_phase3c_abb_grid_meter.sh
```

## Roll back

Use the exact backup archive printed by the installer:

```sh
bash rollback_phase3c_abb_grid_meter.sh \
  /root/kinetics_phase3c_abb_grid_backup_TIMESTAMP.tar.gz
```
