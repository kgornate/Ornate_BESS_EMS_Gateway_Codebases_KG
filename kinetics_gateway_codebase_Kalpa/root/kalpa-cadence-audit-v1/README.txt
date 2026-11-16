KALPA S2 cadence audit alongside overnight throughput capture

Run: bash run.sh
Capture ends 11:00 AM IST, 3 November 2026. Works independently of SSH but
does not survive gateway reboot. No Central Sync restart, source/config
change, control write, upload pause or queue/archive deletion is performed.
The existing overnight observer continues. Keep both result bundles.

REFERENCE: 2,506 S2 canonical definitions / 9,529 expected four-pair + meter
instances extracted directly from the supplied master v1.1 Excel. Workbook
filename and SHA256 are preserved in reference.json and the run metadata.
The frozen deployed manifest checksum is checked against config and its
policy/substream/interval definitions compared with the Excel reference.

CAPTURE: live outbox every 5 seconds (recent 120s) and archive fallback every
60 seconds; deduplicated message IDs. Source DBs are read-only; SQLite writes
are only to /root/kalpa-cadence-audit-UTC/audit.db. Retains all-stream message
ID/time/byte/record summaries and S2 per-runtime-signal emissions with policy,
interval, retention, trigger reason and value+quality hashes. Raw sensor
values, secrets and credentials are not saved. Read work is bounded at
8 MiB payload per cycle, 4s between DBs, and 1.5s per query. Missing readings
and cleanup/archival races are explicitly flagged. This adds observation
overhead; it cannot promise to capture rows removed before they are read.
The local evidence DB can grow to hundreds of MB or more over a long run.

ANALYSIS: Excel metadata drift, periodic emissions earlier than interval
minus 2s, change emissions with unchanged adjacent value+quality hashes,
early heartbeat candidates, late-gap review candidates and point coverage.
Creation time is envelope time, not an exact hardware sampling instant.
Source cache availability, scheduling delays, resets and missed captures
must be considered before calling a timing candidate a confirmed violation.
Unknown trigger reasons and unobserved points are reported. Missing hardware
or idle signals cannot be certified. Other streams get message-level timing
and volume evidence; this is NOT their full per-field cadence certification.
The audit cannot see every hardware poll or independently prove change
detection correctness between emitted samples, calibration or cloud storage.

OUTPUT: audit.db, capture.jsonl, signals.json, report.json, summary.txt, run.json
and a compressed results bundle. DB contains timestamps/IDs/hashes only,
not payloads. The summary never automatically certifies all data correct.
Run path stored at /root/kalpa-cadence-results-path.txt.
Final signal analysis and compression may take several minutes after 11 AM.

Follow: journalctl -u kalpa-cadence-audit -f -o cat
Tests: python3 -m unittest -v test_audit.py
