KALPA UPLOAD DASHBOARD v1.2

Run: kalpa-upload-dashboard
One snapshot: kalpa-upload-dashboard --once
Show cumulative counters, retained states and diagnostics: kalpa-upload-dashboard --details
Without journal reads: kalpa-upload-dashboard --no-logs
Slower view: kalpa-upload-dashboard --interval 5 --db-interval 20

Installation on DEBIX (root):
  tar -xzf kalpa-upload-dashboard-v1.2.tar.gz
  cd kalpa-upload-dashboard-v1.2
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
with --details. Default view prioritizes speed and the accepted-volume table. Existing
status-derived attempted-byte/HTTP rates are labelled estimates and are not
substituted for accepted-volume measurements.

Close the previous viewer with Ctrl+C, install this release, then reopen the same
kalpa-upload-dashboard command. No gateway service restart is required.

NEW IN v1.2: UPLOAD SPEED AND DESIGN REFERENCE

Main screen adds a prominent speed summary with accepted decimal MB/min, msg/min,
records/min, MB/s and records/s; completed HTTP requests/min split into HTTP 2xx,
HTTP errors and no-response failures; attempted batch JSON MB/min; and median,
p95 and largest completed-request body size. Retries/failures count toward the
attempted volume only. Batch JSON sizes include transport envelopes, whereas
accepted bytes count individual logical-message envelopes. Neither is wire usage.

The request window is read from the existing live outbox transport_attempts
journal. Its attempted_utc is written by record_attempt after post_batch returns:
these are completed attempts by completion time, not request-start rates or
currently active requests. No token requests are counted. No request is sent by
this viewer. Query work budget 0.5s, lock wait 0.3s, max 10,000 attempts; failures
or incomplete scans show UNAVAILABLE, never partial totals. Requests and ACKs
use the same window end but are separate snapshots, not a single transaction.
Already-processed ACKs remain excluded from new acceptance. Archive recovery
traffic is excluded from these live windows and not estimated from old snapshots.

Your supplied planning image is displayed as a REFERENCE PLAN, not a guarantee
or certification of capacity. It states gateway workload ~5.5-8 MB/min,
~140-150 msg/min, ~45-50 req/min, typical request ~100-200 KB, largest observed
~1.14 MB, roughly 1s average response needed. Backend targets are >=15-16 MB/min,
>=300 msg/min, >=90-100 req/min, >=2 MB bodies and ideally p95 latency <1.5s.
These historical planning values are not auto-adjusted to current stream settings.
No record/min target was supplied; none is invented.

Current code records no per-request elapsed duration. Mean/p95 response TIME
therefore remains UNAVAILABLE. Request-size p95 is a byte-size percentile, not
response latency. Independent backend DB/CPU/capacity/commit metrics likewise
require backend instrumentation. Observed throughput at this gateway load does
not measure maximum backend throughput. No uploader changes are installed.

Timeouts are labelled TRANSPORT FAILURE instead of BACKEND REJECTION. Saved
archiver/recovery running flags are STALE / UNAVAILABLE if older than 60 seconds
or the main service is not active. Lines wrap instead of silently cutting off
metric values. --details shows the longer cumulative/status/replay/log sections.
A terminal of at least 100 columns keeps the per-stream table on single lines.
