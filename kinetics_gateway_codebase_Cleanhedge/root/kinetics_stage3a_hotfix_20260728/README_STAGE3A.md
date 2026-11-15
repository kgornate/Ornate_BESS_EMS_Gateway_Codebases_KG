# Kinetics Gateway Stage 3A hotfix

Scope: memory, CPU, historian, and no-SD fallback stabilization only. The field-validated BMS/PCS control sequence is not changed.

Changes:
- Uses `/mnt/ems-logs/kinetics-gateway` only when `/mnt/ems-logs` is a real mounted filesystem.
- Uses bounded root-filesystem fallback storage while the SD card is absent.
- Fallback policy: 30 s history sample, 3-day retention, 0.5 GiB quota.
- Replaces per-poll full SQLite `COUNT/SUM/AVG` scans with in-memory counters.
- Batches all asset history rows into one SQLite transaction.
- Uses zlib level 1 instead of level 6 for lower embedded CPU load.
- Avoids rewriting unchanged alarms on every poll.
- Reduces retained WebSocket delta history from 2000 to 200 events.
- Refreshes the snapshot storage-status block at a bounded interval.

Not included in Stage 3A:
- `fresh=true` single-flight/rate limiting.
- Runtime-monitor read-path optimization.
- Flutter polling changes.

Do not reopen Flutter or run charge/discharge tests until Stage 3B is installed and verified.
