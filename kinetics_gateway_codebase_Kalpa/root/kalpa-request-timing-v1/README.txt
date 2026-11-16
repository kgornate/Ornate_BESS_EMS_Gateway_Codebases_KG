KALPA per-request phase timing - observational patch

On DEBIX: bash run.sh
Requires existing deployed Central Sync. Uses only its existing HTTPX/httpcore
and Python standard library. No package downloads. Preflight validates exact
source anchors, checksums, syntax, HTTPX signature and an actual loopback HTTP
trace request BEFORE changing files. Unknown layouts fail closed.

Files patched: app/central_sync/{client.py,batcher.py,uploader.py}; adds
timing_probe.py. Existing batch membership tracing and generation counters
are preserved. No batch limits, cadence, timeout, compression, retry, priority,
ACK interpretation, archive/recovery scheduling or transport lock changes.
The observer adds small in-memory timings and a bounded 50ms-lock SQLite
write after each batch. Timing failures cannot change ACK/upload decisions.

A brief Central Sync restart is REQUIRED. The existing overnight/cadence
observers are temporarily SIGSTOPed during the install/restart and resumed
in finally, to prevent them exiting when Central Sync briefly goes inactive.
Their deadlines continue. external_changes.jsonl is added to their result
folders with UTC boundary markers. Producer/uploader status counters restart;
the overnight summary may flag its full-window deltas unavailable. Use raw
samples by started_utc segment tomorrow. Durable generation metrics and queues
remain intact. This is an explicit measurement interruption, not a single
unchanged overnight baseline. The recorder cannot recover timing before install.

Captures until 11 AM IST, 3 November 2026, then timing writes automatically
stop. Tracing decorators remain installed with minimal inactive overhead;
restore the printed source backups in a later maintenance window to remove.
At ~11:02 AM a transient systemd timer exports /root/kalpa-request-timing-results-
UTC/{requests.jsonl,summary.json,summary.txt} and matching tar.gz. Timer and
observer survive SSH drops but not reboot. Timing DB already written survives
reboot at /var/lib/ornate-central-sync/request_timing.db. Manual export anytime:
  /usr/bin/python3 /root/kalpa-request-timing-v1/report.py
Result path stored in /root/kalpa-request-timing-results-path.txt.

Per batch: request_id, groups (stream/substream/count), message_count,
logical_bytes, source_db, prepare_ms, transport lock_wait_ms, auth_ms,
send_and_ack_total_ms, ack_apply_ms where ACK processing occurs, and each
underlying ingest attempt's trace phases (including one 401 refresh/retry).
No headers, sensor values, token contents, passwords or response bodies saved.
Error type only. TCP/TLS setup, send headers/body, response header wait/body,
and failures are timed via async HTTPX trace events. Version-specific events
are retained as duration names. Missing phases are unavailable/not applicable,
NEVER assumed zero. Reused connections usually have no new TCP/TLS events.
Connection setup includes DNS/TCP where combined by transport; DNS cannot be
isolated here. Waiting for response headers includes network + proxy + backend
time. It is NOT an independent measurement of backend compute/database time.
Authentication, HTTP and overall batch timers overlap; do not sum all fields.
413 parent totals may include split child operations; interpret separately.
Prepare excludes preceding idle sleep; total excludes measurement DB writes.

SQLite keeps at most ~100k requests (cleanup every 100 inserts). Report raw
rows plus mean/p95/max per phase and HTTP outcome. Ingest and archive replay
share the existing serializer; source_db distinguishes them. Auth endpoint
requests are measured by auth_ms, not separately traced with request headers.
No throughput or all-data-delivered guarantee is introduced.

Restore backup client.py,batcher.py,uploader.py with Central Sync stopped,
then start it to remove source instrumentation. Keep timing_probe.py if other
patched files still import it. Do not roll back unrelated gateway patches.

Tests: python -m unittest -v test_timing.py (full source fixture test skips
when the original baseline is not bundled). On-device check-only also performs
the real HTTPX trace compatibility smoke test.
