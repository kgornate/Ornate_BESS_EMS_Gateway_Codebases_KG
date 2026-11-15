# Phase 5.8.1.3 — Isolated reliability boundaries

This cumulative gateway patch addresses the field incident where cache/status
requests remained alive for 120–263 seconds, scheduler retries accumulated,
HTTP capacity returned 503, and the Control page appeared frozen.

## Architectural corrections

- Independent HTTP lanes: **safety**, **scheduler/liveness**, and **general UI**.
  Scheduler or UI congestion cannot consume Abort/Safe Stop capacity.
- The scheduler snapshot reads an immutable dedicated cache and never acquires
  the main gateway lock, accesses SQLite, or performs Modbus I/O.
- Only one scheduler/liveness request can execute at once.
- Scheduler snapshot timeout is four seconds, followed by exponential backoff
  with jitter and a local circuit breaker. Timed-out requests cannot create a
  retry storm.
- Every automatic control stage publishes a real runtime stage. Verification
  publishes the current asset and point and checks operator cancellation
  between reads.
- Existing validated BMS/PCS register values and physical sequence semantics
  are unchanged.

## Mandatory installation state

1. Automation disabled and saved.
2. All four PCS units stopped at 0 kW.
3. BMS contactors open.
4. Run verification first.

```bash
bash verify_phase5_8_1_3.sh
CONFIRM_IDLE=YES bash install_phase5_8_1_3.sh
```

The installer leaves automation disabled and verifies that the canonical
scheduler snapshot returns in under two seconds before restarting the scheduler.

## Rollback

```bash
CONFIRM_IDLE=YES bash rollback_phase5_8_1_3.sh
```
