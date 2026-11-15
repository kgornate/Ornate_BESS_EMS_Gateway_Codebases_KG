# Phase 5.7.1 — Cumulative control truth and safe-stop progress

This package includes every Phase 5.7 control-plane resilience change and adds
the Control-page reconciliation/progress fix. Install this package alone on the
verified Phase 5.6 baseline; do not install the superseded Phase 5.7 ZIP first.

## Cumulative Phase 5.7 resilience

- Offloads blocking telemetry snapshots and alarm reads from the asyncio loop.
- Handles closed WebSocket transports as normal disconnects.
- Reserves HTTP admission capacity for health, login and control writes.
- Keeps routine status GET requests in the bounded general lane.
- Adds Flutter single-flight/backoff polling and removes diagnostics bursts.

## Phase 5.7.1 control truth and progress

- `POST /api/control-sequence/safe-stop-all` returns `202 Accepted` promptly.
- The unchanged safe-stop algorithm continues sequentially in the bounded
  control executor: zero command, zero verification, PCS stop, stop
  verification, pair BAU open and open-contactor verification.
- Compact all-pair status carries a lock-protected `safe_stop_all` operation
  document with run ID, current pair/step, per-pair progress and failures.
- Each verified pair immediately publishes the authoritative terminal overlay,
  so Control does not wait for a later background poll to show 0 kW/stopped.
- The Control page uses the canonical background PCS/rack snapshot plus the
  control operation overlay; it no longer displays old automatic startup steps
  during Safe Stop All.

No Modbus register, command value, shutdown order, tolerance or safety
interlock is changed.

## Backend installation

All pairs must be safely stopped before restarting the gateway:

```sh
bash verify_phase5_7_1_control_truth_safe_stop_progress.sh
CONFIRM_IDLE=YES bash install_phase5_7_1_control_truth_safe_stop_progress.sh
```

The installer validates the Phase 5.6 service baseline, backs up every replaced
backend file, runs compile/tests, restarts the gateway and waits for readiness.

## Flutter update

Copy the three files under `flutter_payload/lib/` into the matching Flutter
project paths, then rebuild the Windows application. The API client is included
as the cumulative verified companion.
