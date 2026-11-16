KALPA 20-minute continuous live upload observer

On DEBIX: bash run.sh
Follow: journalctl -u kalpa-live-upload-test -f -o cat
Keep using kalpa-delivery-dashboard in another terminal.

Runs independently of SSH under systemd for 1200 seconds (22-minute safety
limit). No outage is introduced, no configuration/source is changed, no
services restarted and no operational databases written. Requires Central
Sync already active, uploader unpaused and D1 disabled. It refuses another
active live/outage test. Do not clear queues, restart or change settings
during this capture. Observer read work uses bounded metadata scans and
bounded ACK payload reads; sensor values are not saved.

Output: /root/kalpa-live-test-20min-UTC/ and matching .tar.gz bundle.
summary.txt: generation and acceptance averages, backlog start/end,
HTTP code counts, review flags.
report.json: structured results, per-producer counts, per-stream/substream
live ACK message/record/MB totals, generation byte counters by substream.
samples.jsonl: 10-second snapshots (producer counters, generation bytes,
pending live + SD deduplicated inventory, stream ACK/reject counters,
recovery state, RAM/free disk/load/network counters and service memory/CPU).
http_batches.jsonl: newly observed LIVE HTTP attempts, request IDs, codes,
logical byte sizes, filtered error reasons and traced message IDs / stream /
substream membership if the batch tracing patch is installed.
live_ack_evidence.jsonl: message IDs, stream/substream, acceptance status,
records/bytes and acknowledgement times observed during the run.
run.json: timing, measurement limits and whitelisted non-secret settings.

Generation byte measurement requires kalpa-generation-mb-v1 already installed.
If unavailable, the observer still collects other evidence and marks the
missing byte metric explicitly. New counters / restarted producers invalidate
affected deltas; gaps and read budget failures are reported, not hidden.

Limits: this is not a complete all-message generation ledger. ACK evidence
is sampled from retained LIVE rows; direct archive replay ACK volumes are
not reconstructable and are excluded. Accepted data can include the queue
present before capture started. HTTP 200 is not a message ACK. HTTP duration
is not available in the existing attempt schema. Combined live + SD pending
counts deduplicate copied IDs but are not an atomic snapshot across databases.
No claim that every generated record is delivered is made automatically.
No independent backend database query is performed. Wi-Fi byte/drop counters
are cumulative interface counters, not cloud throughput or current RF loss.

Tests: python3 -m unittest discover -p 'test_*.py' -v
