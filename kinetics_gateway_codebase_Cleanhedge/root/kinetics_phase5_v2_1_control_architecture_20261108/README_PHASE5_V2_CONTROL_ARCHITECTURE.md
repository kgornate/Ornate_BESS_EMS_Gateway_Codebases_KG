# Phase 5 v2.1: control convergence and fresh telemetry architecture

Use this package only after Phase 4 v2 is installed and every BESS pair is safely stopped. The earlier Phase 5 v1 package is withdrawn and must not be installed.

## What this changes

- Replaces repeated 36-point live sweeps inside the automatic sequence with stage-specific reads.
- Reads back every power setpoint immediately and publishes it into the control feedback cache.
- Keeps normal background polling eligible between short control transactions.
- Requires three consecutive authoritative PCS samples before automatic success.
- Performs a final contactor/fault safety check before success.
- Compares PCS power with rack voltage x rack current as an advisory telemetry-consistency diagnostic.
- Makes Safe Shutdown converge runtime from `running` through `aborting` to `aborted` (or `failed`).
- Lets Flutter distinguish `updating`, `communication_error`, `stale`, and `fresh`.

No Modbus register, scaling, sign convention, hardware write order, or protection threshold is changed. There is no database scan in this package.

## Stage read scope

| Stage | Read purpose |
|---|---|
| PCS configuration | documented fault words, PCS state, actual power and setpoint |
| Precharge | fault words, rack voltage, precharge/contactors and PCS input feedback |
| PCS startup | precharge/contactors, battery/DC-bus voltage, PCS state and startup faults |
| Power precheck | direction limits, voltage matching, contactors, PCS state/faults and power |
| Power tracking | only PCS setpoint, actual power and operating state per sample |
| Final safety | documented faults, contactors, precharge and PCS fault/state feedback |
| Consistency | rack voltage, rack current and PCS actual power |
| Safe shutdown | precharge, contactors, PCS state, actual power and setpoint |

## Gateway installation

1. Confirm all pairs are stopped and the scheduler is inactive.
2. Extract this ZIP under `/root`.
3. Run:

```bash
cd /root/kinetics_phase5_v2_1_control_architecture_20261108
CONFIRM_IDLE=YES bash install_phase5_v2_control_architecture.sh
```

The installer checks exact Phase 4 hashes, creates a rollback archive, stops the gateway, installs two backend files, runs the control/API/arbiter/reliability tests, restarts the gateway, and checks health. Any failure restores the original backend automatically.

## Flutter source installation (Windows PowerShell)

From the extracted package directory:

```powershell
.\apply_flutter_phase5_v2.ps1 -FlutterRoot "C:\path\to\kinetics_flutter_dashboard_v2"
```

Then run the printed `dart format`, `flutter analyze`, and rebuild commands.

## Rollback

With all pairs stopped and the scheduler inactive:

```bash
cd /root/kinetics_phase5_v2_1_control_architecture_20261108
CONFIRM_IDLE=YES bash rollback_phase5_v2_control_architecture.sh /root/kinetics_phase5_v2_control_architecture_backup_TIMESTAMP.tar.gz
```
