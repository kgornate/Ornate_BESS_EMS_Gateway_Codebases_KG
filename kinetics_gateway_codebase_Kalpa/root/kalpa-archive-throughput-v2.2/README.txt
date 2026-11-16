KALPA ARCHIVE THROUGHPUT v2.2 + S4 ALARM FORMAT
Requires exact deployed archive-budget v2.1 source. Existing 6-to-20 MB/min
operator budget file is preserved. No new concurrency or larger requests.

CHANGES
- Reuses one archive store/uploader for up to 60 seconds, then reselects.
- Skips repeated full pending counts during archive selection; uses existence.
- Removes the fixed 1-second sleep after a completed replay batch (50 ms yield).
- Runs explicit archive cleanup at most once per 60 seconds per active session.
- Releases the active store on no-send, error, shutdown or reselection.
- Live health, byte charging, cooldowns, partial ACK processing remain active.
- Adds archive selection, store-open and housekeeping time counters.
- Future S4 severity: critical->Critical, alarm->High, warning->Warning,
  info->Info; states ACTIVE/CLEARED. alarm maps to the middle High tier.
  Unknown severity fails explicitly rather than silently downgrading it.

INSTALL (root)
  /opt/ornate-ems-gateway/venv/bin/python -B install.py --check-only
  /opt/ornate-ems-gateway/venv/bin/python -B install.py

HISTORICAL REPAIR (separate explicit step)
  /opt/ornate-ems-gateway/venv/bin/python -B repair_alarms.py
  /opt/ornate-ems-gateway/venv/bin/python -B repair_alarms.py --apply
Only the six previously identified S4 IDs with saved invalid_payload evidence
are repaired, in pending/retry state. Original full rows and replacement
payloads are fsynced to a private /root/kalpa-alarm-format-backup-* audit before
any update. Unknown enums or inflight targets abort planning before changes.
The helper briefly stops Central Sync while changing rows, restarts it in a
finally block, and is safe to rerun (canonical rows are skipped).
Message/event IDs, sequence, timestamps, attempt count, state and original saved
rejection are preserved. Only enums, serialized byte count and retry due time
change. Messages are NOT marked delivered. Backend must ACK corrected retries;
if it reports an immutable-payload conflict, retain the audit for backend review.
A failure part-way through leaves audit/applied.json and may leave some rows
corrected; rerun after resolving the cause. No unacknowledged messages deleted.

VERIFY
  /opt/ornate-ems-gateway/venv/bin/python -B verify.py --duration 300
Share the resulting tar.gz. Faster delivery is an expectation to measure on
the gateway, not a guaranteed network-only rate. Existing long captures continue;
segment counters at both intentional service restarts.

ROLLBACK
Installer prints the exact source rollback command. It restores source only;
it does not reverse completed backend ACKs or historical payload corrections.
Historical originals remain in the separate alarm repair audit backup.
