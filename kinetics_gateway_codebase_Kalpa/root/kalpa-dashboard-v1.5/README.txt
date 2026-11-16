KALPA terminal delivery dashboard v1.5 — queue coverage reporting

cd /root
 tar -xzf kalpa-dashboard-v1.5.tar.gz
cd kalpa-dashboard-v1.5
/opt/ornate-ems-gateway/venv/bin/python -B install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python -B install.py
kalpa-delivery-dashboard

Close an existing dashboard with Ctrl+C before opening the new view.
Central Sync is neither stopped nor restarted by this installer.
No queues, archives, generation or upload settings are changed.

Why: ~50 archives and ~95k pending messages can exceed the viewer's bounded
read window. Cached/incomplete totals can jump when coverage changes. Such
numbers must not be interpreted as current exact backlog or delivery progress.

Fix:
- Suppress current numeric total/per-stream waits while queue coverage is incomplete.
- Show sampled/verified DB coverage and oldest cache age.
- Retain the last complete per-viewer observation with its historical timestamp.
- Group repeated refresh warnings by reason rather than listing every archive.
- Record failed read attempt times so slow archives rotate fairly with other work.
- Keep existing read limits; do not increase CPU/SD scan budgets.

Bounded refresh notices can still appear. This patch improves fairness and
truthful presentation; it does not promise all archive reads will fit the budget.
A complete observation is assembled from per-DB samples, not one cross-DB transaction.
Startup may need several refresh cycles before complete coverage is available.
Pending-ID examples remain cached samples when coverage is incomplete and say so.
ACK windows have separate coverage and remain independent of pending totals.

Installer verifies exact v1.4 source hashes and package integrity, syntax and
tests. Replaces only dashboard.py and monitor_evidence.py; metrics and queue
helpers are included only for offline tests. Tests cover existing read-only
behavior, overlap deduplication, covering indexes, large queues, stale complete
observations, recovery after failed reads and failure-rotation bookkeeping.

Backups are /root/kalpa-dashboard-v15-backup-<UTC>/.
To roll back, close dashboard viewers and restore dashboard.py and
monitor_evidence.py from the selected backup into /opt/kalpa-delivery-dashboard/.
Do not overwrite later dashboard modifications without checking them first.
Existing background recorders keep their loaded version until restarted.
