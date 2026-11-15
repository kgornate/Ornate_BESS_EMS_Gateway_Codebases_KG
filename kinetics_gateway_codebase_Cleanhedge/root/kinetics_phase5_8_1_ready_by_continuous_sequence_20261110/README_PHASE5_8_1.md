# Phase 5.8.1 — Ready-by two-phase automatic sequence

This patch is cumulative over the field-installed Phase 5.8 baseline.

## Architecture

- The requested charge/discharge start is now an **active-by** boundary.
- Solar qualification begins at the preparation boundary (default 15 minutes early).
- The existing automatic worker performs PCS configuration, BMS precharge, PCS start and power permission checks, then pauses at verified zero power.
- The same run ID and the same Control-page step list are retained.
- When Flutter opens after the scheduler has already started, the Control page hydrates the selected pair's detailed steps from the gateway cache only; it does not add fieldbus reads.
- Power ramp and tracking resume shortly before the active-by boundary (default 120 seconds early).
- BMS limits and direction permission are revalidated immediately before the first non-zero dispatch.
- All pair dispatch requests are accepted quickly; bounded per-pair workers and the existing I/O arbiter serialize physical fieldbus traffic safely.
- Solar loss, stale solar, configuration change, window end, worker failure or restart reconciliation use the validated Safe Stop All path.
- Timestamped transition records are stored in `/mnt/ems-logs/kinetics-gateway/automation_events.jsonl` with 5 MiB rotation.
- Event records include preparation durations and the measured early/late offset when all enabled pairs reach target.

## Expected 09:00 example

- 08:45 preparation window opens.
- Solar must remain above 100 kW for 60 seconds.
- Pairs 1–4 are sequentially prepared and remain ready at 0 kW.
- 08:58 power dispatch begins.
- All enabled pairs are targeted to verify charging by 09:00.
- 14:30 Safe Stop All begins.

Exact completion remains dependent on device response and communication health; the system records actual timestamps and never bypasses verification to meet a clock boundary.

## Installation order

1. Run Safe Stop All and confirm every pair is stopped.
2. On the gateway, run `CONFIRM_IDLE=YES bash install_phase5_8_1_ready_by_continuous_sequence.sh`.
3. Close the running Flutter application.
4. From an elevated PowerShell in the extracted package, run `powershell -ExecutionPolicy Bypass -File .\install_flutter_phase5_8_1.ps1`.
5. Format the six patched Dart files, run `flutter analyze`, and then run `flutter run -d windows`.
