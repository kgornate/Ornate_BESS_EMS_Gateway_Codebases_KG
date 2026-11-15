# Kinetics Gateway Phase 1.7 + 1.8

This incremental patch targets memory attribution and a safe cache correction.

## Phase 1.7 instrumentation

- Extends `/api/diagnostics/runtime` with process RSS/PSS, anonymous/file RSS,
  cgroup anon/file/kernel memory and startup-stage timing.
- Adds `tools/phase1_memory_probe.py`, a bounded JSONL sampler that does not call
  historian APIs or access telemetry payloads.
- Records hardware-cache initialization duration after the API process is live.

## Phase 1.8 correction

- After the existing startup database accounting scan, issues Linux
  `POSIX_FADV_DONTNEED` for the SQLite database and WAL.
- This is advisory cache reclamation. It does not delete, truncate, vacuum,
  rewrite or migrate the database.
- It does not alter polling, Modbus, control sequence, Flutter API, historian
  retention or scheduler behavior.

## Deferred Phase 1.6 limitation

Large historian queries remain unapproved. Do not run `limit=5000`, parallel
historian tests, exports or Flutter large-history plotting during validation.

