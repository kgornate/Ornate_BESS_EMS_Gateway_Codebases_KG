# Phase 5.6 — Safe-stop reconciliation and PCS fault capture

This incremental patch is based on the installed Phase 5.5.1 baseline.

It fixes the field-observed condition where Safe Stop All physically stopped
all pairs, but the Control overview continued to show old 5 kW values until
slow background polls converged. Each pair now publishes its verified terminal
zero-power/stopped/contactors-open snapshot immediately after its sequential
safe-stop completes. Terminal safety feedback may override stale background
PCS values until a later control stage replaces it.

PCS startup failure reporting now also captures module fault word 10 (0x1219)
and latched fault word 7 (0x1226). This improves Pair 4 diagnosis without
clearing, ignoring or bypassing the PCS fault-shutdown state.

No command value, register write, power tolerance, sequence order or safety
interlock is changed. Phase 5.5.1 distinct-sample behavior is retained.

Install only after Pair 1 and all other pairs are safely stopped:

```sh
CONFIRM_IDLE=YES bash install_phase5_6_safe_stop_reconciliation.sh
```

No Flutter change is required.

Optional pre-install verification:

```sh
bash verify_phase5_6_safe_stop_reconciliation.sh
```

The installer prints the exact rollback archive. To restore it:

```sh
bash rollback_phase5_6_safe_stop_reconciliation.sh \
  /root/kinetics_phase5_6_safe_stop_reconciliation_backup_<timestamp>.tar.gz
```
