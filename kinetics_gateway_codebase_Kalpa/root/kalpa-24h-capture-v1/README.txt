KALPA 24-HOUR DIAGNOSTIC CAPTURE v1

Run on the DEBIX after the 30-minute validation has finished:
  cd /root
  tar -xzf kalpa-24h-capture-v1.tar.gz
  cd kalpa-24h-capture-v1
  bash run.sh

The launcher runs five focused connection-lifecycle checks, verifies exact
v2.0.1 deployed recovery source and package checksums, backs up that source,
closes archive selection SQLite connections explicitly on success/error,
and restarts Central Sync once before capture. Restart resets service counters;
the capture starts after the fresh instance is verified.

No cadence, batch size, timeout, archive policy or queue contents are changed.
No outage/pause is introduced. This measures current behavior, not an already
certified configuration. Live upload delays and archive backlog growth remain
under investigation; do not expect the cleanup alone to fix throughput.

The existing active dashboard recorder is restarted with a 25-hour runtime
limit, appending the existing log. If it was inactive, a uniquely named recorder
is launched and current-path.txt updated. Keep earlier portions of the log for
analysis, but analyze the new post-cleanup service instance separately.

Capture: kalpa-archive-transport-validation.service, samples every 15 seconds.
Duration: 86400 seconds from launcher execution, not a fixed clock deadline.
Results path is printed by the launcher. Completion creates .tar.gz automatically.
The systemd capture and recorder survive an SSH disconnection. This transient
capture does not automatically resume after reboot; partial samples remain.

Progress (Ctrl+C closes only journal viewing):
  journalctl -u kalpa-archive-transport-validation -n 5 --no-pager -o cat

Captured: durable per-stream generation counts/JSON bytes; live uploader
acceptance/rejection/request counters; archive acceptance bytes/records and
request counters; new archival counters; live queue age/size/capacity; service
instance/boot identifiers; RAM/load/CPU/service memory. Dashboard separately
records rolling per-stream ACK rates and sampled HTTP results/message IDs.

Limits: no complete end-to-end per-message delivery ledger, backend storage
verification, exact network bandwidth or strict Excel cadence certification.
Counters must be segmented if service restarts; queue status can be cached up
to 30 seconds. HTTP 200 alone does not mean every message was accepted.

Tomorrow, share the printed result .tar.gz and a compressed dashboard log:
  LOG=$(cat /root/kalpa-dashboard-logs/current-path.txt)
  gzip -c -- "$LOG" > "/root/kalpa-dashboard-logs/share-$(date -u +%Y%m%dT%H%M%SZ).log.gz"

Connection cleanup backup: /root/kalpa-sqlite-cleanup-backup-<UTC>/
Installer auto-restores the original source on failed startup verification.
For a later manual rollback, first stop Central Sync, verify no later source
changes would be overwritten, restore overflow_recovery.py from that backup,
and start Central Sync. No database/SD archive rollback is needed.
