KALPA terminal delivery dashboard v1.6

Problem: the v1.5 viewer exits with more than 127 archive databases, even while
Central Sync continues operating. Large backlogs also make retaining every
pending message ID in the viewer unnecessarily expensive.

Install on DEBIX:
 cd /root
 tar -xzf kalpa-dashboard-v1.6.tar.gz
 cd kalpa-dashboard-v1.6
 /opt/ornate-ems-gateway/venv/bin/python -B install.py --check-only
 /opt/ornate-ems-gateway/venv/bin/python -B install.py
 kalpa-delivery-dashboard

Close old viewer with Ctrl+C before reopening. Existing background viewers
load new code only after they are restarted. Installer does not restart services.

Design:
- Removes the 127-archive exit. Checks up to 4096 archives; larger inventories
  remain explicitly partial and do not disable live monitoring.
- Pending cache retains per-database/per-substream counts and at most five
  example IDs per DB, rather than all pending IDs across the backlog.
- Two-second pending and ACK refresh scheduling budgets remain, with bounded
  individual queries. Work may finish slightly after a scheduling deadline.
- Slow pending/ACK reads rotate fairly. Unchanged archive receipt rows are
  cached but filtered against the moving 60-second window every frame.
- ACK retained cache and combined frame are each limited to 20,000 rows.
- Queue labels now explicitly say COPY COUNTS: duplicate copies across live
  and archives are not deduplicated. Never interpret their sum as unique work.
- ACK totals still deduplicate message IDs across live/archive evidence.
- Incomplete queue counts remain withheld. ACK partial coverage remains explicit.
- All sampled databases are opened read-only. No indexes/schema are changed.

The viewer is read-only; current snapshots are per-DB observations, not one
cross-DB transaction. Archive count is not an undelivered-message count.
Initial coverage can require multiple refresh cycles on a busy SD card.
Large single archives can still hit bounded query limits; this is reported,
not silently treated as an empty queue.

Verification: eight tests including 160 archives, compact large-row summaries,
copy counts vs ACK deduplication, expired cached receipts, changed archives,
partial coverage with live ACKs still displayed, and enforced read-only access.

Installer verifies all four uploaded source hashes and package checksums.
Only dashboard.py and monitor_evidence.py are replaced. metrics.py and
queue_metrics.py are test fixtures; deployed versions remain untouched.
Backups: /root/kalpa-dashboard-v16-backup-<UTC>/.
Guarded rollback command is printed after installation.
