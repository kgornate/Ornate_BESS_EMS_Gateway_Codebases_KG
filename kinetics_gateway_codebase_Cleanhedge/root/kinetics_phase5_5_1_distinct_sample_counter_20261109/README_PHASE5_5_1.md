# Phase 5.5.1 — Distinct PCS sample counter hotfix

Phase 5.5 correctly required distinct coherent PCS samples, but a repeated
observation of the same cache timestamp reset the consecutive-match counter.
Because UI/control polling is faster than PCS polling, the counter could not
exceed one even while actual power matched the target.

This hotfix ignores duplicate cache observations. Only a new eligible PCS
sample can increment or reset the counter. No register, threshold, tolerance,
command order or safety behavior is changed.

Install only after Pair 1 and all other pairs are safely stopped:

```sh
CONFIRM_IDLE=YES bash install_phase5_5_1_distinct_sample_counter.sh
```

No Flutter change is required.
