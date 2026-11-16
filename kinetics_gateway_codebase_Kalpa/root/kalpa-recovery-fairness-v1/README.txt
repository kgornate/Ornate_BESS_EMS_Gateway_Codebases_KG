KALPA ARCHIVE RECOVERY FAIRNESS v1 -- 3 November 2026

PURPOSE
The supplied gateway recovery status showed all 370 checks blocked (299 live,
71 load), and zero archive opens/requests. This patch provides controlled
recovery opportunities under moderate live traffic, keeping strict guards
against excessive live backlog, memory pressure and high system load.

INSTALL ON DEBIX (after copying this archive to /root)
cd /root
tar -xzf kalpa-recovery-fairness-v1.tar.gz
cd kalpa-recovery-fairness-v1
/opt/ornate-ems-gateway/venv/bin/python -B install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python -B install.py
python3 verify.py

Preflight checks the exact SHA256 of the recovery source you supplied; refuses
unknown/changed recovery code and existing policy files. Runs 20 offline tests.
Install backs up recovery source, adds a small policy helper and a JSON sidecar.
It restarts Central Sync only if already active. Generation pauses briefly
while that service restarts; other gateway services are not restarted.
A runtime-status check confirms the new recovery counters are present.
The installer never opens a queue database or deletes queued/archive messages.
Once restarted, normal recovery is allowed to send and apply real backend ACKs.

POLICY
Existing admission checks remain the normal, low-pressure path.
When those checks repeatedly postpone recovery, a bounded opportunity can be
admitted if all of these hold:
- No manual transport pause; live probe is available.
- Available memory >= configured minimum (currently 500 MB).
- Live pending/retry/inflight count <= 25.
- Oldest live message <= 30 seconds (no age limit when live count is zero).
- Load <= max(existing max_load1, logical CPU count * 2.0).
  On a two-CPU system this is 4.0. This is a configurable initial guard,
  NOT a measured saturation limit. Unknown/nonfinite load fails closed.
- Two consecutive eligible checks; at least 30 seconds since last admission.
- No three-sample strictly growing live queue increasing by >=10 to >=20.

A small normal live queue no longer needs to be younger than three seconds
for every recovery opportunity. An empty live queue can qualify at moderate
load. Timer NEVER bypasses manual pause, low RAM, the opportunity load/age/count
limits or the growth guard. Continued pressure may still correctly block replay.
Policy timers are monotonic and reset on service restart. JSON changes take
effect after Central Sync restarts. Sidecar:
/etc/ornate-ems-gateway/recovery_policy.json

Existing request limits are preserved: archive <=25 messages / 131072 logical
bytes per prepared batch, 10-second pause after a sent cycle; shared HTTP client
and transport lock. No parallel HTTP worker is introduced. Normal low-pressure
admission can use the existing cadence; additional bounded opportunities occur
no more than every 30 seconds. Existing HTTP 413 splitting/auth retries retain
existing behaviour. A request already in flight can delay a new live request.
Live priority is bounded scheduling, not preemption of an active HTTP request.

This is a conservative first recovery policy, not a guaranteed fast drain rate.
128 KiB every 30 seconds is at most about 0.262 MB/min before request overhead,
response time and rejections. Low-pressure normal admissions can be faster.
Actual drain requires backend per-message acceptance AND spare capacity beyond
live generation. Raising delivery capacity may be needed for a large backlog.

WHAT IS PRESERVED
Main central_sync.json unchanged. All generation/Excel cadence/control logic,
ACK-release patch, auth/timeouts, live batching, retries, archive copying,
archive selection and per-message ACK semantics remain unchanged. Archive files
remain retained after replay, as before. No automatic physical SD-file deletion.
Old backend S2 failures or permanently malformed payloads are not repaired here.

OBSERVATION
python3 verify.py
journalctl -u ornate-central-sync.service -n 50 --no-pager -o cat

Recovery status gains:
  counters.bounded_opportunity_count -- gate admissions, not accepted messages
  counters.pause_opportunity_count -- waiting for interval/eligible samples
  resource_gate.recovery_policy.mode -- normal / bounded_opportunity / paused
  resource_gate.recovery_policy.reason and thresholds
Existing request_cycle_count means uploader run_once cycles; a cycle may find
no due batch. last_success_utc is transport success, not proof all messages ACKed.
The old last_pause_reason is a last-result field and may briefly remain stale.

Capture 10-15 minutes after install. Check recovery requests advance, archived
unacknowledged counts decrease and live queue/age remain bounded. Inspect actual
archive ACK/rejection results if requests advance without backlog reduction.
The old dashboard's query-budget warnings are a separate viewer issue; unchanged.

ROLLBACK
Installer prints the exact backup path. Use:
python3 install.py --rollback /root/kalpa-recovery-fairness-backup-TIMESTAMP

Rollback verifies installed file hashes before restoring original source and
removing helper/policy. Refuses to overwrite later source/policy edits. Restarts
Central Sync if currently active. No database contents are rolled back; valid
ACKed payloads released by running recovery are not recreated.

VALIDATION
20 offline tests: observed 14-message/16-second/2.458-load scenario resumes;
bounded admission rate; original normal path; manual pause, RAM, queue size,
queue age, unavailable metadata, unknown load guards; growing queue; recovery
following pressure; sidecar validation; real patched gate counter integration;
installer/rollback roundtrip; later-edit protection and simulated write failure.
Other recovery methods were structurally compared with supplied source.
Not yet executed on your DEBIX hardware or validated against the live backend.
