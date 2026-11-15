# Kinetics Gateway Phase 1.6.1 — Historian Workload Containment

## Purpose

Phase 1.6 correctly bounded total HTTP admission, but field validation showed that eight admitted `limit=5000` historian requests could still perform expensive SQLite reads, payload decompression, and JSON serialization concurrently. This corrective patch independently bounds historian work.

## Changes

- Allows one historian query/export to execute at a time by default.
- Rejects parallel historian work quickly with HTTP 503 and `Retry-After: 1`.
- Caps interactive JSON historian responses at 1000 rows.
- Keeps CSV historian export available up to 5000 rows, serialized through the same workload gate.
- Adds `/api/historian/status` counters for accepted, rejected, active, and peak historian work.
- Adds configuration validation and a concurrency regression test.

## Non-goals

This patch does not delete telemetry, change the 90-day retention policy, alter stored fields, vacuum the database, or change Flutter/server data contracts. Local historian redesign will follow stabilization and field-usage mapping.
