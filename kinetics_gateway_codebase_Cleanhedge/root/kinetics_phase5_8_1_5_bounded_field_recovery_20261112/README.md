# Kinetics Phase 5.8.1.5 — Bounded Field Recovery

This patch is based on the exact installed Phase 5.8.1.4 scheduler captured on
2026-11-12. It does not replace the Phase 5.8.1.4 gateway HTTP-admission,
cache-only status or bounded-runtime changes.

## Changes

- Requires three consecutive fresh active-readback failures before Safe Stop All.
- Uses an authoritative fresh pair read to reject transient cached mismatches.
- Adds a 45-second post-dispatch readback grace period.
- Records the cached and fresh values that caused every readback decision.
- Inhibits a new preparation when less than 480 seconds remain in the window.
- Classifies Solar/PCC evidence as healthy, low, stale, offline or bad/missing.
- Adds one bounded PCS state-1024 recovery attempt per pair per schedule window.
- State-1024 recovery is eligible only for the observed PCS-start failure with
  no module/latched fault words reported.
- Recovery performs zero-power preparation, pair-safe-stop, verifies state 1024
  cleared, and retries the original preparation once.
- Failure of recovery causes the existing plant-wide safe stop and fault latch.

## Installation preconditions

1. Disable automation in Flutter and press **Save automation settings**.
2. Verify all four pairs are safely stopped.
3. Copy and verify the ZIP on the gateway.
4. Run:

```sh
CONFIRM_IDLE=YES bash install_phase5_8_1_5.sh
bash verify_phase5_8_1_5.sh
```

The installer deliberately leaves automation disabled. Re-enable it only after
reviewing the installation and verification output.

## Runtime tuning

Defaults are intentionally conservative and can be overridden in the scheduler
service environment if field evidence later requires adjustment:

- `AUTOMATION_ACTIVE_READBACK_CONFIRMATIONS=3`
- `AUTOMATION_ACTIVE_READBACK_GRACE_SECONDS=45`
- `AUTOMATION_MIN_RESTART_REMAINING_SECONDS=480`
- `AUTOMATION_PCS_1024_RECOVERY_TIMEOUT_SECONDS=180`

