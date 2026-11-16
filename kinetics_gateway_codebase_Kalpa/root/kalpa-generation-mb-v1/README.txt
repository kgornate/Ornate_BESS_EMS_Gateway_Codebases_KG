KALPA generation MB/min - v1

Requires the existing kalpa-delivery-dashboard, preserving HTTP batch tracing.
Run with the gateway venv Python:
  python install.py --check-only
  python install.py
  kalpa-delivery-dashboard

The installer briefly stops/restarts Central Sync only if it was active.
Existing timed upload tests must finish first. No config, cadence, HTTP limits,
ACK/retry policy, control or archive/recovery logic is changed.
Backup of outbox.py and dashboard.py is printed. Restore those files while
Central Sync is stopped to roll back; the unused metrics table can remain.

Newly committed outbox messages add logical serialized UTF-8 JSON bytes to
generation_metrics_v1 in the SAME transaction as enqueue. Both the normal
enqueue and S2 atomic path are covered; duplicates, failed transactions,
HTTP retries, archive copies and direct replay do not add generation bytes.
Counters have one row per stream/substream and survive ACK cleanup/archival.
This adds a small SQLite update per enqueue transaction.

The viewer computes a rolling approximately 60-second counter delta,
decimal MB (1,000,000 bytes), warming for the first minute. A shorter warm-up
window is normalized per minute and labelled warming. Missing counters show
unavailable, never a fabricated zero. Recreated counters reset the window.
Only generation AFTER installation is counted, not the old backlog.
Producer msg/min and records/min remain their original status-counter rates;
their sample boundaries can differ slightly from generation bytes.
Live accepted MB/min uses logical message bytes with recent retained live
ACKs, can include older data and excludes direct SD archive recovery ACKs.
It is not on-wire bandwidth, backend storage volume or exact cohort delivery.

Verification: python -m unittest -v test_generation.py
