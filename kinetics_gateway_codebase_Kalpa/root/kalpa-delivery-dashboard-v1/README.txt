KALPA DELIVERY DASHBOARD v1 - terminal only, standard Python 3

Install: bash install.sh
Run: kalpa-delivery-dashboard
Snapshot: kalpa-delivery-dashboard --once
Inspect full retained ID: kalpa-delivery-dashboard --message UUID
Slower refresh: kalpa-delivery-dashboard --interval 10

Recommended terminal width 140 columns, height 60+ lines. Screen refresh defaults
to 5 seconds. Native ANSI heading colors, no browser/HTML or third-party packages.
Ctrl+C closes the viewer only. Existing two dashboards remain installed.

Shows actual producer-counter message/record rates, warming over the observed
window up to roughly 60 seconds. It never substitutes retained-row counts for
the producer rates. Producer restart/counter reset creates a new baseline.

Accepted volumes are actual trailing-60s retained LIVE ACK evidence, with
already_processed separate. They may include older queued data. Archive replay
ACK volume and generation byte counters are not provided by the current schema.
No all-data-delivered percentage or guarantee is fabricated.

Live + SD queues are read with SQLite mode=ro/query_only. Copied IDs are deduped;
retained ACK wins over a stale copied pending record. Cross-file snapshots are
not atomic. The inventory is capped at 64 archives, 12000 rows per database and
1s query work per DB, lock wait 0.2s. Error/limit means unavailable, not zero.
ACK/window helper budgets and redaction are retained from upload dashboard v1.2.

Recent generated-message IDs show exact state, attempts, location and last
recorded error/backend detail. Recent HTTP batches show full request IDs, HTTP
status (NO RESPONSE when no status exists), message/byte counts and actual detail.
The current transport_attempts table lacks message-ID membership. Therefore the
viewer DOES NOT associate a batch HTTP code with individual messages by guess.
The --message lookup searches current retained live + archive rows only.
After cleanup old evidence may be absent. Durable lifecycle recording is a
separate gateway instrumentation addition, not part of this read-only viewer.
Per-S2-substream producer rates are likewise unavailable; accepted volumes and
pending counts are shown per substream. Queue/accepted totals show stream detail.

Status/errors are sanitized for common secrets and terminal controls. No
credential environment file is read. Config/DB/service/upload behavior is not
changed, and no new producer, uploader, HTTP call, port or daemon is started.
Refreshing the view is not a change to producer cadence.

Offline checks: python3 -m unittest discover -s . -p test_dashboard.py -v
