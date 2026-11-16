KALPA V2.2 CONTINUOUS ARCHIVE + LIVE OBSERVATION
Run: cd /root/kalpa-v22-archive-24h-v1 && bash run.sh

One continuous 24-hour process, sampled every 15 seconds. No hourly chaining.
Read-only production access: small status files and bounded live DB queries.
No archive DB enumeration, telemetry payload capture, or production restart.
Existing second 24-hour/48-hour capture is left running.
run.sh stops only temporary v2.2 one-hour or chained-hourly observers if active.
Do not reboot during observation: transient systemd units do not survive reboot.
SSH can disconnect safely.

Bounded memory: at most 10000 receipt IDs in a 10-minute rolling dedup cache;
128 stream counters; 1024 segments per component; samples streamed to disk.
Disk captures status metadata every 15 seconds, not repeated receipt histories.
Reports live and archive counter deltas in separate service-instance segments.
Stale status excluded from summary counters but retained in raw samples.
Rates exclude unknown gaps between service segments; not complete delivery proof.
Generation resets detected; missing history cannot be recovered.
SD per-message ACK receipts are not scanned; this is an aggregate recovery test.

Progress: journalctl -u kalpa-v22-archive-24h -n 8 --no-pager -o cat
Path marker: /root/kalpa-v22-archive-capture-path.json
During capture: summary.partial.json checkpoint each minute.
At completion: summary.json, completion.json, samples.jsonl and one tar.gz.
SIGTERM/SIGINT writes partial final reports and bundle. Sudden power loss may
leave only raw samples and the last partial checkpoint. Those remain useful.
Completion is observer completion, not a claim all backlog was delivered.
