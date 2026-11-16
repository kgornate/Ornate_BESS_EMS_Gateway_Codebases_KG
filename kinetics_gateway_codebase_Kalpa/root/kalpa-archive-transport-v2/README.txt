KALPA DEDICATED ARCHIVE TRANSPORT v2 + DELIVERY DASHBOARD v1.4
3 November 2026

WHAT CHANGES
- One archive recovery worker now owns its own CentralBackendClient / HTTP pool
  and asyncio transport lock. Live uploader keeps its original client and lock.
  Maximum intended ingest concurrency: 1 live + 1 archive. This is concurrency
  within Central Sync, not another daemon. Existing shared CPU, uplink, SD and
  backend capacity still apply; increased speed is a test hypothesis.
- Share only the live client's existing token broker/cache/refresh lock. This
  avoids competing token issuances for the same gateway. Archive ingest HTTP
  connections are independent; token refresh may briefly wait for the broker.
- Adaptive archive pacing: catch-up 10s when live <=10, oldest<=15s, load<=1.5
  per logical CPU; limited 30s when live <=25, oldest<=30s, load<=2 per CPU.
  Two healthy observations required. Genuine pressure yields and recovery uses
  hysteresis before resuming. Memory floor remains configured 500MB. Current
  128KiB/25-message archive caps remain. Live batching/cadence unchanged.
- Recheck actual health AFTER auth, immediately before each archive ingest HTTP
  attempt (including 401 retry and 413 split). Manual pause/stop/pressure defers
  safely. Unsent inflight leases return to retry with payload and attempt count
  preserved; conditional ownership predicates protect other claims/ACKed rows.
- HTTP429 Retry-After pauses the WHOLE archive worker; 5xx/network failures and
  retryable per-message rejections add a 30s archive cooldown. Existing per-row
  retry/backoff/ACK parsing still used. This does not guarantee backend acceptance.
- Archive selection skips segments with only future-due retries so they do not
  block other due archives. Physical archive retention is unchanged.
- Instance-scoped cumulative accepted message/record/byte counters for archive
  recovery, duplicate counters separate, rejections/missing ACKs/HTTP attempts.
  Byte metric failures explicitly counted; do not certify bytes if nonzero.
- Dashboard v1.4 shows transport type, adaptive mode, interval and cumulative
  archive ACK totals. Last-60s live/SD/TOTAL receipt rates retained. Queue metadata
  for unchanged archive DB+WAL signatures is reused, reducing work/partial reads.
  Inode, ctime, mtime and size signatures are used; all reads remain read-only.

INSTALL (root on DEBIX)
cd /root
tar -xzf kalpa-archive-transport-v2.tar.gz
cd kalpa-archive-transport-v2
/opt/ornate-ems-gateway/venv/bin/python -B install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python -B install.py

Exact-source preflight guards known deployed client/uploader/recovery and
current v1.3 dashboard/helper hashes. Also guards Outbox eligible/lease API AST
and requires ACK-release patch, one live worker, known 128KiB recovery cap.
Refuses a changed baseline; do not force past a mismatch. No queue DB mutations
by installer. Backs up all changed files/settings and auto-restores on startup
verification failure. Restarts only Central Sync if active. Restarts existing
kalpa-delivery-recorder if active to load viewer v1.4. Other gateway services and
main central_sync.json are untouched. Generation pauses briefly at restart.
New editable sidecar: /etc/ornate-ems-gateway/archive_transport.json.
Old recovery_policy.json/helper remain available for rollback; unused in v2.

VALIDATE FIRST -- DO NOT START 24H UNTIL REVIEWED
bash observe.sh
journalctl -u kalpa-archive-transport-validation -f -o cat

This starts a read-only 30-minute status/counter capture under systemd, survives
SSH disconnect, and bundles results under /root/kalpa-archive-transport-validation-
TIMESTAMP.tar.gz. No artificial outage or upload pause. Compact capture uses
status files + small generation counter table; no whole-archive scans. Status
includes archiver counters (new spill), live and archive outcomes, service/boot
identity, CPU/RAM/load. Archive cumulative bytes are exact for successful receipt
reads within that service instance; live byte ACK rates remain dashboard windows.
Observer does not automatically call this pass/fail or certify all-stream cadence.
A reboot stops the transient observer unit: retain partial samples for diagnosis.

After successful validation, same observer may run 24h explicitly:
bash observe.sh 86400
Do not change settings or deploy patches mid-test. Counter resets must be segmented.

LIVE DASHBOARD
kalpa-delivery-dashboard --interval 15
If recorder was inactive, start it using your usual fresh-log command. Existing
logs are retained; restarted recorder may append a v1.4 section to its existing
file. Prefer only one viewer/recorder to reduce monitoring overhead.

PASS CRITERIA FOR THE 30-MIN COMPARISON
- Recovery transport_mode=dedicated_archive_client; running=true.
- Archive accepted bytes/messages increase faster than baseline ~0.15 MB/min,
  accounting for generation/load changes. No promise of a fixed multiplier.
- Live queue size/age remain bounded and repeatedly recover after bursts.
- No persistent growth in rejection, missing-ACK or HTTP failure rates.
- Archive additions are low; accepted recovery exceeds additions over the window.
- Archive ACK byte metric error count stays zero; no service/boot resets or
  unexplained capture gaps. Backend still needs valid per-message acknowledgements.

ROLLBACK
Installer prints:
python3 install.py --rollback /root/kalpa-archive-transport-backup-TIMESTAMP

Verifies installed hashes before reverting; refuses later edits. Restarts Central
Sync if currently active. Reopen/restart viewer/recorder afterward. No data is
restored/deleted by rollback; messages validly ACKed during operation stay delivered.

VALIDATION
45 offline tests including actual supplied uploader ACK parser/client methods
with fake HTTP, simultaneous live wait + archive completion, independent HTTP
objects, shared token broker, post-auth pre-send checks, safe lease deferral,
missing/duplicate/retry ACK handling, 429 cooldown, archive backoff selection,
client ownership/closure, pressure guards, viewer index plans/read-only SQL,
>12k metadata rows, changed/unchanged archive cache refresh and installer rollback/partial-write/later-edit protection.
No actual backend or device speed guarantee: first run the 30-minute validation.
Reference client/uploader sources and dashboard metrics/queue_metrics are testing
fixtures ONLY; installer does not replace those production modules.
