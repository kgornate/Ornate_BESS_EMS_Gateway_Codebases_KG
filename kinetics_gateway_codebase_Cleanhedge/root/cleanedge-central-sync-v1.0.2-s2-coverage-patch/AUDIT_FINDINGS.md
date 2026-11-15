# Audit findings

Evidence captured from the deployed gateway on 2026-11-13 showed:

- Bank SOC/SOH were already correct engineering percentages.
- Rack SOC/SOH and inner SOC were per-mille values forwarded without conversion.
- S1 field names declared volts and megaohms while source values were mV and kOhm.
- Rack current-limit source keys used `_limit`; the adapter requested `_lim`.
- S10 expected flat pair objects while CleanEdge supplies nested
  `pair/runtime/summary/workflow` objects, resulting in `no_enabled_pairs`.
- The auxiliary meter exposed IEEE-754 float bit patterns as unsigned integers.
- The S2 producer excluded that meter because a runtime vendor tag was absent,
  despite the frozen manifest containing 31 approved auxiliary-meter points.
- Pair-specific BMS bank views could multiply the one canonical system-bank
  definition; rack telemetry already represents per-pair data.
- The aggregate bank carried `rack_id=1` and was classified as a rack before
  its `bms_bank` identity was considered, withholding 150 bank/EMS records.
- Five canonical PCS register keys had semantic runtime aliases, withholding
  20 records across four PCS assets.
- Independent producer clients repeatedly requested large snapshots, causing
  transient HTTP 503 responses.

S3 health, S4 alarm wire formatting, S8 configuration audit and the PCS
engineering values examined in S1/S2 did not require contract changes.
