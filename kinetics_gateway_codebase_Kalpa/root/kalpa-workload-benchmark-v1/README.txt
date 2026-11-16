KALPA GENERATED WORKLOAD BENCHMARK v1

Run on DEBIX with Python 3 standard library. No pip packages required.
The existing gateway backend must already be running. Central Sync config
must have enabled=true (as configured in this session).

  python3 /root/kalpa-workload-benchmark-v1/benchmark.py --minutes 30

This stops and starts ONLY ornate-central-sync.service, counts newly generated
durable messages in its existing database, and stops that service at completion.
The gateway backend, Wi-Fi, cloudflared, existing configuration and queue remain
in place. Upload retries remain enabled; no internet is required for capture.
Do not run another generation service concurrently. Do not modify configuration
or clock during measurement. Leave this SSH session open until completion.
Ctrl+C saves a partial report and stops Central Sync.

Output directory: /root/kalpa-workload-<UTC timestamp>/
  summary.txt: steady-state totals and caveats
  streams.csv: all/startup/steady per-stream and S2 substream metrics
  seconds.csv: total counts/bytes for every fixed one-second bin, including zeros
  report.json: machine-readable results, configuration hash and method notes

Metrics: message count, record count, messages/s, records/s, logical JSON bytes,
KiB/s, MiB/hour, projected GiB/day, mean/p95/max logical-message bytes, and
maximum fixed-1-second counts/bytes. Per-message gzip level-1 sizes are included
for comparison ONLY; these are not actual compressed batch/network bytes.

Interpretation:
- Prefer the 29-minute steady window; first 60 seconds are restart observations.
- Durable S2 checkpoints and S4/S8 cursors survive restart. This does NOT measure
  a clean first-install baseline and does not force all 9494 signals to emit.
- TOTAL and per-stream rows use substream='*'. Other rows are breakdowns; do not
  sum both breakdowns and aggregate rows. Phase rows also overlap ('all' is the
  union of 'startup' and 'steady').
- S5/S6/S7 disabled: no workload for this deployment. Enabled but idle S4/S8
  streams mean no emission was observed, not zero worst-case event traffic.
- S9 is command results, not a periodic producer. No commands/events are injected.
- No real physical control actions, configuration edits or event storms are
  triggered. S10 reports current status only under the existing control gates.
- Data is observed from the currently deployed real producer code/configuration.
  Gateway mode (usually mock) is recorded. This benchmark is evidence for that
  exact run; physical-site change rates and rare-event peaks remain unmeasured.
- Fixed one-second peaks use producer message creation time, not exact commit,
  transport or backend arrival time. Backend arrival peaks may differ.
- Reads are bounded and release SQLite snapshots before JSON/gzip processing.
  Observer overhead and the existing uploader retry workload affect measurements.
- Persistent sequence allocation deltas are reconciled against captured message
  counts. A mismatch or zero captured messages fails the capture-completeness
  check. It can indicate missed rows or failed message generation; investigate
  either before giving the backend team definitive sizing figures.
- Existing queue rows are excluded; retransmission attempts do not count as new
  generation. Rows must remain in the live DB until read (poll approximately 1s).
- Outbox is finite; if capacity is reached, producer output can stall. Check
  Central Sync logs for errors before treating measured rates as normal cadence.
- The report contains aggregates/configuration hash, not credentials or payloads.
- Actual HTTP batches, ACK throughput, CPU/RAM and server capacity are not measured.

Start Central Sync again later when desired:
  systemctl start ornate-central-sync.service

Validation: three tests pass, including exact aggregate/percentile/bin tests and an end-to-end synthetic
SQLite run with mocked service control. Actual DEBIX 30-minute run is performed
by the user; no production benchmark numbers are pre-generated here.

Wi-Fi may stay connected. The existing uploader continues its configured
authentication/upload/retry behaviour. ACKed rows are counted too; this is still
a generation benchmark, not a delivered-throughput benchmark.

After completion, collect the report (replace TIMESTAMP with the printed name):
  tar -czf /root/kalpa-workload-results.tar.gz -C /root kalpa-workload-TIMESTAMP
Then from Windows PowerShell:
  scp -O root@192.168.10.2:/root/kalpa-workload-results.tar.gz "$HOME\Downloads\"

The dashboard refreshes every 10 seconds. It shows cumulative captured messages,
records, average KiB/s and p95 size for all ten slots. Detailed S2 substream and
startup/steady tables are saved in streams.csv. Read summary.txt first.
