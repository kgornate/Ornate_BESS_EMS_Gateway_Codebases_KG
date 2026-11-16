KALPA UPLOAD DASHBOARD v1

Run: kalpa-upload-dashboard
One snapshot: kalpa-upload-dashboard --once
Without journal reads: kalpa-upload-dashboard --no-logs
Slower view: kalpa-upload-dashboard --interval 5 --db-interval 20

Installation on DEBIX (root):
  tar -xzf kalpa-upload-dashboard-v1.tar.gz
  cd kalpa-upload-dashboard-v1
  bash install.sh
  kalpa-upload-dashboard

This is a separate terminal viewer. It leaves kalpa-sync-dashboard (generation
view), gateway code/configuration and running services unchanged. It makes no
HTTP requests and starts no producers, uploader, recovery or command poller.
Ctrl+C closes only the viewer. Configuration and status JSON are read with a
2 MiB limit. It never reads the credentials environment file.

Readouts:
* HTTP request and per-message outcome counters are from existing uploader
  status. A 2xx HTTP result can contain message-level rejection or missing ACKs.
* Per-stream ACKs and errors show all configured S1-S10 streams, including idle
  and disabled entries. Old rejection counts can coexist with a later accepted
  result because these are cumulative counters for that uploader instance.
* Recent rates are deltas between fresh status timestamps. They reset when the
  uploader instance/counters change. The attempted logical JSON byte rate includes
  retries; it is not confirmed throughput, compressed wire bytes or generation.
* S2 substream counts use a mode=ro, query_only SQLite connection and a 0.75s
  query-work budget. SQLite lock wait is limited to 0.3s. Counts are retained
  rows only in the live database, excluding overflow archives and rows cleaned
  after ACK. Zero evidence does not prove rejection, non-generation or failure.
  These queries do not load full record payloads.
* Detail queries, filesystem stats, service state and filtered journal reads
  refresh every 10s by default. The screen/status refresh interval is 2s.
* Archive sizes include DB/WAL/SHM, not an invented number of queued messages.
  Existing replay-marker files are counted but not treated as proof that those
  archives remain drained: a marker can become stale if an hourly archive grows.
  Directory metadata scan is capped at 4096 archive files.
* Recovery service status includes its resource-gating pause reason. The
  recovery-uploader JSON is only the most recently written uploader instance
  snapshot; it is not an all-time cumulative archive-upload total. Absence can
  simply mean recovery is gated or has not made a request. Status ages are shown.
* Zero values in absent/stale status are not live measurements; the screen marks
  the stale/unavailable state. Do not sum counters from unrelated uploader runs.

Metrics are gateway-observed delivery outcomes. Backend CPU, server database
writes, frontend visibility and request latency are not exposed by the current
status files. A gateway ACK does not independently verify database commit or
frontend display. Backend instrumentation would be required for those metrics.

Last-error/journal text is filtered for terminal escapes and sensitive key/value
fields, bearer tokens and recognizable JWTs. It is not a guarantee against a
backend response containing unlabelled confidential text; review any output
before sharing. No output is automatically transmitted or persisted.

Tests: python3 -m unittest discover -s . -p test_dashboard.py -v
