# Kinetics Phase 5.8.1.8 — Runtime resilience

This bounded patch reduces false plant-wide stops caused by brief telemetry or
HTTP admission disturbances. It retains Phase 5.8.1.7 PCS state-1024 recovery
and every confirmed PCS, BMS, scheduling and low-solar safety response.

## Changes

- Separates valid low solar from unavailable/stale solar telemetry.
- Fresh solar below the configured threshold still uses the configured
  `stop_hold_seconds` (currently 30 seconds).
- Stale, offline or bad-quality solar telemetry enters a degraded state and
  must remain unavailable for 120 seconds before safe-stop.
- Active pairs remain active during short solar telemetry gaps; prepared pairs
  are not dispatched until fresh solar returns.
- A retryable HTTP 503 on a pair compact-status read preserves the same run ID
  and retries for up to 15 seconds.
- Explicit failed/aborted runtime status and non-retryable errors still fail
  immediately.
- Adds recovery/degradation evidence to status and event logs.

## Installation

Disable automation in Flutter, Save, and verify all four pairs safely stopped.

```sh
CONFIRM_IDLE=YES bash install_phase5_8_1_8.sh
bash verify_phase5_8_1_8.sh
```

The installer validates the exact Phase 5.8.1.7 scheduler baseline, backs it
up, runs regression tests, restarts only the scheduler, and leaves automation
disabled.

## Rollback

With automation disabled and all pairs safely stopped:

```sh
CONFIRM_IDLE=YES bash rollback_phase5_8_1_8.sh
```
