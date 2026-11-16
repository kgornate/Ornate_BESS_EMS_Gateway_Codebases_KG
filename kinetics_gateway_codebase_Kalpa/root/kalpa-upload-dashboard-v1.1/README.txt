KALPA UPLOAD DASHBOARD v1.1

Run: kalpa-upload-dashboard
One snapshot: kalpa-upload-dashboard --once
Show previous cumulative/retained-state tables too: kalpa-upload-dashboard --details
Without journal reads: kalpa-upload-dashboard --no-logs
Slower view: kalpa-upload-dashboard --interval 5 --db-interval 20

Installation on DEBIX (root):
  tar -xzf kalpa-upload-dashboard-v1.1.tar.gz
  cd kalpa-upload-dashboard-v1.1
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

NEW IN v1.1: ACCEPTED VOLUME PER MINUTE

Default compact view includes a trailing 60-second ACK window: accepted logical
messages, records, and decimal MB of original logical-message JSON, by stream and
S2 substream. Window counts are actual retained live-outbox ACKs, not generation
rates or an extrapolation from the last 2-second screen refresh. The displayed
window end time remains fixed between detail refreshes (default every 10s).

A record means an element of records[]: for example an S1 BESS-pair record or an
S2 signal record. It does not mean one SQL row in the backend. Message byte counts
include the logical envelope, but exclude HTTP batch wrapper, gzip, TLS and
server storage overhead. MB uses 1,000,000 bytes; existing MiB labels remain
binary units. These are backend-ACK-observed metrics, not independent server DB
commit instrumentation.

Already-processed ACKs are counted separately as duplicate ACK messages, records
and JSON volume. They are excluded from new accepted/min totals. A successful
retry is counted in the minute of its ACK, even when its original data timestamp
is several days old. Failed attempts and pending/retry rows are excluded.

The metric query uses a separate read-only connection, 0.3s lock timeout, 1.0s
query-work/processing budget, 10,000-message and 64 MiB payload-read limits.
If a budget, parse or schema failure occurs, totals become UNAVAILABLE; partial
sums are never displayed as a healthy zero. Valid empty windows do show zero.
It does not add indexes or change gateway databases.

Archive-recovery ACKed rows are immediately cleaned by the existing recovery
code. Current recovery status has message counters but lacks ACKed record/byte
counters. Consequently this read-only viewer cannot reconstruct complete archive
record/MB rates. It explicitly shows those rates as unavailable and keeps last
recovery uploader snapshots separate. Live ACK volume does not include recovery.
Exact aggregate live+recovery volume requires additive uploader instrumentation
or a backend metrics endpoint; no such changes are included here.

The previous cumulative stream and retained S2 state tables remain available
with --details. Default view prioritizes the new accepted-volume table. Existing
status-derived attempted-byte/HTTP rates are labelled estimates and are not
substituted for accepted-volume measurements.

Close the previous viewer with Ctrl+C, install this release, then reopen the same
kalpa-upload-dashboard command. No gateway service restart is required.
