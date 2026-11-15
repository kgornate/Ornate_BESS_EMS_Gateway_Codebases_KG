# Kinetics Phase 5.8.2.0 — Isolated automation HTTP path

This patch closes the field-observed failure where the local automation
supervisor timed out after four seconds while the gateway eventually returned
the same cache-only snapshot six to ten seconds later.

## Architectural changes

- Gives the automation snapshot a dedicated one-worker executor instead of
  FastAPI/AnyIO's shared worker pool used by telemetry, historian and
  diagnostics endpoints.
- Keeps the existing exclusive scheduler admission lane and localhost guard.
- Uses async scheduler authentication so authentication cannot queue behind
  shared synchronous workers.
- Keeps the snapshot cache-only: no Modbus, SQLite or live refresh is added.
- Makes the scheduler deadline configurable through
  `AUTOMATION_SNAPSHOT_REQUEST_TIMEOUT_SECONDS`, with a default of 15 seconds
  and a safety floor of 10 seconds.
- Retains bounded backoff and the active-operation snapshot-loss safety bound.
- Bounds legacy 5000-row alarm-history requests to 500 rows per transaction
  and reports truncation explicitly.

## Preserved safety behavior

- Confirmed PCS/BMS failures and prolonged snapshot loss still safe-stop.
- Phase 5.8.1.7's single bounded PCS state-1024 recovery remains unchanged.
- Repeated state 1024 is not hidden and does not cause unlimited recovery.
- Solar low-power and telemetry-loss responses remain unchanged.

## Installation

Disable automation in Flutter, Save, and verify all four pairs safely stopped.

```sh
CONFIRM_IDLE=YES bash install_phase5_8_2_0.sh
bash verify_phase5_8_2_0.sh
```

The installer accepts either the released Phase 5.8.1.8 scheduler or its exact
15-second field hotfix, validates the gateway routes baseline, creates a
rollback backup, runs regression tests, restarts the gateway and scheduler,
and leaves automation disabled.

## Rollback

With automation disabled and all pairs safely stopped:

```sh
CONFIRM_IDLE=YES bash rollback_phase5_8_2_0.sh
```
