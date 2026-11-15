# Kinetics Gateway — Phase 1 Complete Patch

Baseline: Nagpur snapshot captured 2026-10-31 at 08:43:18 UTC.

## Scope

- Adds a cache-only four-pair control/SOC view for the scheduler.
- Adds a bounded compact endpoint for authoritative live pair verification.
- Removes routine scheduler use of the full `/api/bms/racks` payload.
- Treats a timed-out control POST as ambiguous and reconciles state without replaying it.
- Closes control workers, monitor threads, BMS/PCS transports and SQLite on shutdown.
- Adds bounded admission for large telemetry snapshots and live refreshes.
- Extends the gateway stop timeout to allow orderly resource cleanup.
- Adds focused regression tests for the new contracts.

No Modbus address, write value, scaling rule, charge/discharge sequence, SOC
threshold, power convention, pair ordering or existing Flutter response contract
is changed.

## Install

Only install while all four pairs are confirmed idle:

```bash
unzip kinetics_phase1_complete_20261031.zip -d /root/kinetics_phase1_complete_20261031
cd /root/kinetics_phase1_complete_20261031
CONFIRM_IDLE=YES bash install_phase1_complete.sh
```

The installer verifies the exact source baseline, remembers whether the scheduler
was active, stops the scheduler before the gateway, creates a rollback archive,
installs the patch, compiles it, runs four focused tests, starts the gateway,
checks health, and restores the scheduler only if it was active beforehand.

## Verify

```bash
cd /root/kinetics_phase1_complete_20261031
TOKEN='PASTE_INTERNAL_TOKEN' bash verify_phase1_complete.sh
```

During the first deployment, keep charging and discharging disabled and observe
the gateway for 30–60 minutes.

## Full regression suite

Run only while the plant is idle:

```bash
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m pytest -q --disable-warnings --tb=short
```

## Rollback

```bash
cd /root/kinetics_phase1_complete_20261031
bash rollback_phase1_complete.sh
```

The rollback script uses the exact backup recorded by the installer.
