KALPA DELIVERY DASHBOARD v1.3 -- LIVE + SD ACK evidence

WHAT THE UPLOADED CAPTURE CONFIRMED
3 November 2026 09:51:53 to 09:52:53 UTC (15:21:53 to 15:22:53 IST):
- Live uploader accepted_count 731 -> 815 (+84); live queue rows 6 -> 0.
- Recovery cycles 13 -> 15.
- SD pending/retry/inflight rows 23531 -> 23529 (-2).
- End snapshot includes 20 recently retained SD accepted receipts, 1,388 records,
  1.302699 MB across archives. This is NOT a one-minute acceptance total:
  the collection looked back 15 minutes and compact receipt retention varies
  with each database's cleanup. Never subtract these retained totals as lifetime
  counters; cleanup can remove older evidence between samples.
- Specific archives 20261103T08: 51 -> 50; 20261103T09: 147 -> 146.
- Latest recovery uploader instance shows HTTP200 and one accepted BMS message.
Thus archive ACKs AND archive outstanding-count reduction are now observed.

DASHBOARD CHANGES
- Separate LIVE ACCEPTED / SD ACCEPTED / TOTAL ACCEPTED last-60s messages,
  record counts and decimal MB; duplicate ACKs separate. TOTAL deduplicates IDs.
- Per-stream and S2 subgroup live ACK/m and SD ACK/m shown separately.
- Acceptance and backlog coexist: 'ACKs observed; backlog present'.
  Removed misleading unconditional 'pending unique' label.
- Unknown/failed reads shown as partial; never interpreted as no backlog or
  stream rejection. No ACK in one minute does not mean an enabled stream failed.
- Pending samples read existing covering metadata indexes. No payload_json reads
  or new DB indexes. Multiple queries per DB share one read transaction.
- Exact-ID dedup across observed live/SD copies. Pending refresh every 15s;
  sampling age is displayed. Results across DBs are not a global transaction.
- Per-DB read budgets and overall cycle budgets limit monitoring workload.
  Failed reads retain old queue samples with age/partial warning. Work on older
  unrefreshed archives is prioritised in later cycles.
- Recent request trace now includes BOTH live and archive DB requests, explicitly
  labelled LIVE/SD with stream/substream membership and recorded HTTP outcomes.
- Recent message IDs display accepted receipts with LIVE/SD labels.
- Existing generation-byte counters and producer message/record deltas retained.
  No per-substream generation rate is invented; unavailable rates remain '--'.
- --message / --request inspect retained evidence directly, without a global scan.

INSTALL
Close old dashboard window with Ctrl+C. Central Sync keeps running.
cd /root
tar -xzf kalpa-delivery-dashboard-v1.3.tar.gz
cd kalpa-delivery-dashboard-v1.3
python3 -B install.py --check-only
python3 -B install.py
kalpa-delivery-dashboard --interval 10

The installer verifies the exact deployed dashboard.py, metrics.py and
queue_metrics.py hashes against your supplied bundle. Refuses changed source.
Only replaces /opt/kalpa-delivery-dashboard/dashboard.py and adds
monitor_evidence.py. Backs up the old dashboard; no systemd restart, no database
writes, no transport/cadence/recovery/configuration changes. Packaged metrics.py
and queue_metrics.py are test dependencies; they are NOT installed or replaced.
This updates kalpa-delivery-dashboard, not the separate kalpa-sync-dashboard.
Rollback command with exact backup path is printed by installer. Rollback refuses
later edits instead of overwriting them. Reopen dashboard after installing.

LIMITATIONS
This is evidence monitoring, not independent verification of backend DB contents.
ACK retention is short (5 minutes / 5,000 rows per database), so missing cleaned
history cannot be reconstructed. Last-60s totals are observed receipt evidence,
not a generated-message cohort delivery percentage. Absent receipt tables are
explicitly counted; do not interpret them as lifetime zero deliveries.
Read errors make ACK values lower bounds. Pending cache age/partial errors remain
visible. Index absence is reported, not repaired by a full payload-table scan.
Recent trace retention is bounded; unavailable trace does not mean no requests.
Source tables created WITHOUT rowid/expected indexes are unsupported and reported.
Replayed physical archive files remain retained as before. File count/size is
not pending workload. Old backend S2 problems require actual ACK results.

OFFLINE VALIDATION
17 tests including separate live/SD ACKs, overlap deduplication, duplicate ACKs,
covering-query plans, >12,000 pending rows, unavailable indexes/tables, read-only
SQL enforcement, unchanged queue contents, receipt window/count limits and
truthful acceptance/backlog/unknown rendering. Live hardware check still needed.
