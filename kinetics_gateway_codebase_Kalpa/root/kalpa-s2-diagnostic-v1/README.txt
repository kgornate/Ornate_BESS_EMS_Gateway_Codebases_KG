KALPA current S2 diagnostic, 5 minutes

Run bash run.sh. Read-only collection under systemd; SSH may disconnect.
No Central Sync restart, config/source/queue change, outage or upload pause.
No full archive inventory, credential/env export or old heavy ACK payload scan.

Every 15 seconds: whitelisted live uploader/stream ACK/rejection counters and
latest errors, generation counters, compact live ACK receipts, archiver/recovery
state and recovery uploader counters. Start/end sample only oldest 2 + newest
4 archive files (deduplicated), one oldest outstanding message each for BMS,
EMS and PCS substreams, and the live queue. Bounded per-query time/row limits.
Up to 3 raw S2 telemetry envelopes per start/end sample are included for backend
schema comparison; this can contain telemetry values but no auth/env config.
IDs, attempt counts, last error and timestamps link old archived messages to
current retry activity. Examples need not cover every rejection or substream.

Exports current request/batch IDs and groups/membership from existing trace DB
for the capture window, if available. HTTP 200 bodies are not recorded by that
trace; exact per-message response causes may only be reflected by current last
errors / queued rows. Missing/capped evidence is labelled unavailable. Accepted
receipt volume excludes direct archive replay. A successful recovery request
does not mean all messages accepted; compare recovery uploader counter deltas.

Output /root/kalpa-s2-diagnostic-UTC.tar.gz with run settings whitelist, samples,
compact ACK metadata, generation counters, retry and telemetry envelope examples,
HTTP batch trace and completion result. Use it to distinguish improved fresh S2
acceptance from old archive IDs still stuck/deferred. It does not change or fix
backend acceptance, certifies no backlog totals and is not a backend DB audit.
