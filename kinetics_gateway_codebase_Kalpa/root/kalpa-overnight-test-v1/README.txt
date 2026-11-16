KALPA overnight observer - 2/3 November 2026

Run: bash run.sh
Stops CAPTURE at 11:00 AM IST, 3 November 2026; Central Sync continues.
From 6 PM this is approximately 17 hours. Uses device wall clock for deadline
and monotonic elapsed time for recording. Verify date -u if device clock is wrong.
Refuses to start if the deadline is past or more than 24 hours away, if Central
Sync is inactive, uploader paused, D1 enabled, or another known test active.
No test outage, restart, queue clearing, configuration change or control action.
No reboot persistence: SSH can drop, but a gateway reboot interrupts the run.

Records every 30 seconds: producer counters, generation JSON bytes if installed,
live + SD deduplicated backlog, retained live message ACK record/byte evidence,
per-stream ACK/rejection counters and filtered latest reasons, recovery gating,
RAM/load/disk/network counters and service memory/CPU/restart counters.
HTTP attempts are read incrementally (not re-scanning all overnight requests),
with recorded request IDs, codes, filtered errors and traced stream/substream
membership/message IDs when installed. No credentials or sensor values saved.
This is monitoring work and has bounded read overhead. Inventory is limited to
128 archive files and 500,000 metadata rows, 3 seconds per DB. Read errors are
reported instead of returning partial successful totals. No automatic retuning.

Output: /root/kalpa-overnight-test-UTC/ and matching .tar.gz bundle.
The exact path is stored in /root/kalpa-overnight-results-path.txt.
Files: summary.txt, report.json, samples.jsonl, live_ack_evidence.jsonl,
http_batches.jsonl, run.json. Partial reports/bundles are attempted on SIGTERM.

Important evidence limits: direct SD archive replay ACK volumes are excluded
from live ACK totals. Older queued data can be accepted during capture. HTTP
200 is not a per-message acceptance guarantee. Request duration is not recorded
in the current schema. Counters and multiple DB inventories are not atomic;
this is not an independently verified backend commit ledger or full-message
generation ledger. Missing evidence / counter resets invalidate affected rates.
Streams with no generated data cannot be certified. Network counters are
interface-wide cumulative counters, not cloud goodput or current RF loss.

Keep gateway settings unchanged during capture. Do not apply patches, clear
archives/queues or restart Central Sync. Backend changes should be timestamped
for later correlation with the trace, not treated as a single fixed baseline.

Tests: python3 -m unittest discover -p 'test_*.py' -v
