# Kinetics Phase 5.8.1.7 — Pre-reset field recovery

This bounded patch corrects the PCS state-1024 recovery ordering observed on
pair_4 on 2026-11-13. It retains all Phase 5.8.1.6 HTTP snapshot resilience.

## Recovery sequence

For the existing narrow, eligible state-1024 startup failure only:

1. Pair-specific safe-stop/reset with BMS contactors opened.
2. Verify PCS state 1, precharge 0, contactors open, and zero setpoint/power.
3. Run one same-direction, prepare-only automatic sequence at 0 kW.
4. Verify the matching recovery run reaches zero-power readiness.
5. Pair-specific safe-stop again and verify the fully stopped baseline.
6. Retry the original pair preparation once.

The recovery remains limited to one attempt per pair per schedule window. A
hard fault, failed verification, timeout, repeated state 1024, or failed retry
still causes the existing plant-wide safe stop and fault latch.

## Installation

Disable automation in Flutter, Save, and verify all four pairs safely stopped.
Then run:

```sh
CONFIRM_IDLE=YES bash install_phase5_8_1_7.sh
bash verify_phase5_8_1_7.sh
```

The installer validates the exact Phase 5.8.1.6 scheduler baseline, backs it
up, runs regression tests, restarts only the scheduler, and leaves automation
disabled.

## Rollback

With automation disabled and all pairs safely stopped:

```sh
CONFIRM_IDLE=YES bash rollback_phase5_8_1_7.sh
```
