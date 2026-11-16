Read-only companion to recover the overnight observer's frozen ACK evidence.

Cause: original reader repeatedly selects all retained payload JSON since the
test start and interrupts the scan after one second. Its evidence count can
freeze although uploader ACKs continue. This companion does not hot-patch or
restart the running observer or Central Sync. The original frozen count may
remain unchanged. Use the NEW combined_unique_live_ACKs status instead.

Run bash run.sh. Ends 11 AM IST, 3 November. Survives SSH loss, not reboot.
Reads metadata IDs first, skips seen IDs before fetching payload JSON, reads
only unseen ACK payloads by primary key and persists each event immediately.
Each cycle limits new payload reads to about 8 MiB / 3s. Bounded metadata scan
3s / 100k rows. Source DB is opened read-only. No source, config, ACK state,
outbox, archives or existing services are changed. Output files only are new.

Copies the original evidence file incrementally, unions it by message ID
with currently retained live ACKs from the original test start onward, then
continues capturing new ACKs. Partially failed cycles preserve earlier saved
rows. No raw sensor values, credentials or payload JSON retained. Direct
archive replay ACKs are excluded. Previously cleaned missed rows cannot be
recovered; this is not an independent proof of backend database commits.

Output /root/kalpa-ack-capture-UTC/: combined_live_ack_evidence.jsonl,
status.json, summary.json, summary.txt, run.json plus matching tar.gz bundle.
Path recorded in /root/kalpa-ack-capture-results-path.txt. Supply this extra
bundle tomorrow together with the original throughput, cadence and timing
bundles. Use combined ACK data instead of the stalled original ACK totals.
Any overlap is deduplicated by message_id. Existing original samples and
restart markers remain available for timing and counter-segment analysis.
