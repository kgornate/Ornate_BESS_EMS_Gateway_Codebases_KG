KALPA CENTRAL SYNC LIVE DASHBOARD v1

Read-only terminal dashboard for the EXISTING ornate-central-sync.service.
Python 3 standard library only. No pip installs, web port, new producer or
application/configuration patch. install.sh installs only this tool and launcher.

On DEBIX:
  cd /root
  tar -xzf kalpa-sync-dashboard-v1.tar.gz
  bash /root/kalpa-sync-dashboard-v1/install.sh

Start existing generation when desired (this also resumes configured uploads):
  systemctl start ornate-ems-gateway.service
  systemctl start ornate-central-sync.service

Open the display:
  kalpa-sync-dashboard

Default refresh 10 seconds; generation window previous 60 seconds.
All S1..S10 slots and nine S2 substreams appear, even if zero/disabled.
S2 records are individual signal samples; other records have stream-specific
meaning. Substream rows break down S2; don't add them to S2 again.

Metrics: logical messages/records per window, records/s, logical KiB/s,
mean/p95/max message bytes, total MiB/hour projection. Queue section shows
pending/retry/inflight, retained ACKed/dead counts and physical DB+WAL/SHM size.
Queue counts refresh every 30 seconds; sample age is displayed. Generation and
recent service journal lines refresh every display cycle. Known secret/token/
password key-value fields are filtered, and terminal control characters removed.
Do not share raw journal logs without checking them for other sensitive content.

The monitor opens SQLite with mode=ro and query_only=ON; queries use the existing
stream/substream/time index. Record counts are read from the JSON envelopes;
signal values and application credentials are not printed. Reads are bounded
to 10000 messages/window; if exceeded, an incomplete-window warning appears.
Normal SQLite reads can briefly hold read locks; observer work adds CPU/I/O.

Rolling generation is calculated from currently RETAINED live-outbox rows by
producer creation timestamp. ACKed rows count too. Cleanup, overflow archival or
database replacement can reduce visible counts. This is an operational view,
not the audited 30-minute benchmark or proof of per-signal Excel cadence.
The live outbox section excludes external overflow archives. Physical DB size is
different from logical JSON payload size. No HTTP/batch compression is measured.
MiB/hour is a projection from the rolling window; rare event load isn't measured.

The current utility_meter mapping may have no forwarding; zero is no emission,
not proof that all meter signals are covered. Event-driven streams can be idle.
Enabled flags are stream-level settings, not individual substream configuration.
No network is needed for this view. The existing service continues normal upload
attempts whenever it is running, including when Wi-Fi/internet becomes available.

Ctrl+C closes only this VIEW. SSH disconnect closes only this VIEW. The existing
systemd Central Sync producer continues independently; reopen the view later.
To stop production generation/upload attempts explicitly:
  systemctl stop ornate-central-sync.service

One snapshot (useful for copying to backend team):
  kalpa-sync-dashboard --once

Live logs only, with journal timestamps:
  journalctl -u ornate-central-sync.service -n 30 -f

Continuous terminal output without clearing:
  kalpa-sync-dashboard | tee /tmp/kalpa-sync-view.txt
This file grows until stopped; use --once for a bounded capture.

Validation: python3 -m unittest discover -s . -p test_dashboard.py
Four local tests verify aggregation, read-only window queries (including ACKed
rows and cap detection), all stream/substream display, and log secret filtering.
Live DEBIX performance and installation still require user verification.
