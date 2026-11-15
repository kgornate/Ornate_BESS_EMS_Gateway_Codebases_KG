# Phase 5.8.1.4 — Bounded status runtime

This targeted hotfix closes the remaining HTTP saturation mechanism observed
during Pair 4 preparation. `fresh=false` status requests grew from milliseconds
to 21–64 seconds and eventually caused HTTP 503 responses.

## Confirmed root cause

Each completed automatic step retained its complete result. That result
contained a status document, which contained a runtime snapshot, which contained
earlier step results. The object graph therefore grew recursively across stages
and pairs. Flutter continued polling while the backend deep-copied this history,
causing overlapping requests and admission exhaustion.

## Permanent corrections

- Runtime snapshots expose only bounded step metadata; raw step results are
  never nested into runtime again.
- Step completion remains visible with label, status, message and timing.
- Durable events and audit data remain unchanged.
- Cache-only Control reads consume the atomically published cache without
  waiting for the cache publisher lock.
- `/api/health` bypasses work admission and cannot return a capacity-related
  503 merely because UI/control work is occupied.
- BMS/PCS register mappings, writes, thresholds and physical sequencing are
  unchanged.
- Installation restarts the gateway, clearing already-expanded in-memory
  Phase 5.8.1.3 runtime objects.
- The installer stress-tests 32 concurrent cache-only pair-status reads and
  requires every response to complete within three seconds.

## Mandatory installation state

1. Disable automation and Save.
2. Confirm every pair is safely stopped at 0 kW and BMS contactors are open.
3. Close Flutter during installation.
4. Install:

```bash
CONFIRM_IDLE=YES bash install_phase5_8_1_4.sh
```

The installer validates the exact Phase 5.8.1.3 baseline, creates a rollback
backup, runs unit/contract tests, restarts both services, performs the concurrent
status stress test, and leaves automation disabled.

## Post-install verification

```bash
bash verify_phase5_8_1_4.sh
```

## Rollback

Only while automation is disabled and all pairs are stopped:

```bash
bash rollback_phase5_8_1_4.sh
```
