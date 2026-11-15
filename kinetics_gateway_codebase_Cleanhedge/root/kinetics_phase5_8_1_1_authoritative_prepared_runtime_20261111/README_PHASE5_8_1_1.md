# Phase 5.8.1.1 — authoritative prepared runtime

This is a focused gateway scheduler patch on top of Phase 5.8.1. It does not
replace the proven PCS/BMS control implementation and contains no Flutter or
backend API changes.

## Failure corrected

The backend automatic sequence had already completed zero-power preparation
and committed `run_status=prepared`, but the scheduler performed a second gate
using a compact cached telemetry summary. That summary may lag the terminal
control result, so the scheduler incorrectly declared preparation failure and
ran Safe Stop All.

## New behavior

- Preparation succeeds only on the exact accepted `run_id` whose backend
  runtime reports `prepared` at `automatic_prepared_zero_power`.
- Direction, requested power and absence of a runtime error must also match.
- Compact zero-power fields remain event diagnostics, but cannot overturn the
  backend's authoritative terminal result.
- Preparation and dispatch are tied to the same run ID.
- A preparation or dispatch fault is latched for that schedule window. The
  supervisor safely stops once and does not retry repeatedly. Saving a changed
  configuration clears the latch for an operator-requested retry; ending the
  window or disabling automation also clears it.

## Install

First turn **Enable automatic schedule** off in Flutter and press **Save**.
Run Safe Stop All and verify all four pairs are stopped. Then:

```bash
cd /root/kinetics_phase5_8_1_1_authoritative_prepared_runtime_20261111
bash verify_phase5_8_1_1_authoritative_prepared_runtime.sh
CONFIRM_IDLE=YES bash install_phase5_8_1_1_authoritative_prepared_runtime.sh
```

The installer verifies the deployed Phase 5.8.1 baseline, backs up the current
scheduler, runs focused tests, starts the supervisor and verifies that the new
build is running in the disabled state. The gateway service is not restarted.

After installation, enable and save automation when ready for the controlled
test. Control-page progress continues to come from the existing backend
automatic sequence.
