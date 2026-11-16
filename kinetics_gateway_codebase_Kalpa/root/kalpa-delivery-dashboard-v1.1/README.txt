KALPA TERMINAL DELIVERY DASHBOARD v1.1: HTTP batch stream membership

Requires v1 terminal kalpa-delivery-dashboard already installed.

On DEBIX:
  cd /root
  tar -xzf kalpa-delivery-dashboard-v1.1.tar.gz
  cd kalpa-delivery-dashboard-v1.1
  /opt/ornate-ems-gateway/venv/bin/python install.py --check-only
  /opt/ornate-ems-gateway/venv/bin/python install.py
  kalpa-delivery-dashboard

The installer backs up the current uploader and terminal viewer under
/root/kalpa-batch-trace-backup-UTC. It inserts only an import and guarded observer
call after the existing HTTP response returns. It preserves all other deployed
source, validates anchors/syntax, and refuses unknown layouts. It also refuses
to interrupt an active timed test. Previously active Central Sync is briefly
stopped/restarted to load the observer; an inactive service stays inactive.
Configuration, producer cadence, request JSON, batching, ACKs, retries and source
drivers are unchanged. The optional observer writes its own SQLite trace and
has no role in delivery decisions. Trace exceptions are caught by the uploader
and logged by exception class only. SQLite lock wait is capped at 50 ms.

Each NEW completed batch gets exact request_id, source DB, HTTP status, completion
time, original member message IDs and stream/substream message counts. No attempt
is made to infer membership by matching timestamps. Mixed streams are displayed
as multiple groups beneath the batch. HTTP success is still not per-message ACK.
No response is code 0 and displayed NO RESPONSE, never a fabricated HTTP code.

The dashboard's recent HTTP section displays live request history with captured
membership. Both live/recovery uploader calls record the sidecar trace; a replay
request can be inspected by its request ID if retained. Show member IDs with:
  kalpa-delivery-dashboard --request REQUEST-UUID
Existing --message UUID lookup remains retained live/archive message evidence.

Trace file: /var/lib/ornate-central-sync/batch_trace.db
Retention: last 24h, max 1000 completed batches (whichever is shorter); rows are
reused and incremental vacuum is requested. This is not a permanent audit ledger.
Only identity/count/size/outcome metadata and filtered error detail are stored;
no credentials, HTTP headers or telemetry values. Old pre-patch batches cannot
be reconstructed and explicitly show membership not recorded. Existing stream
rate and archival ACK-volume limitations remain unchanged.

Rollback (substitute actual backup directory printed by installer):
  systemctl stop ornate-central-sync.service
  cp /root/kalpa-batch-trace-backup-UTC/uploader.py /opt/ornate-ems-gateway/backend/app/central_sync/uploader.py
  cp /root/kalpa-batch-trace-backup-UTC/dashboard.py /opt/kalpa-delivery-dashboard/dashboard.py
  systemctl start ornate-central-sync.service
If service was originally inactive, leave it stopped instead of the last line.
Unused helper/trace files may remain after rollback; no queue data is deleted.

Offline tests: python3 -m unittest discover -s . -p test_trace.py -v
Four tests: exact mixed membership; timeout/redaction; source preservation and
idempotence; reject unfamiliar source. Actual DEBIX deployment not yet performed.
