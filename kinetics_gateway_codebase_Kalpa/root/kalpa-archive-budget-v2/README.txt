KALPA ARCHIVE BUDGET v2 - CAPACITY TRIAL

Purpose: recover the roughly 23.9 GB of pending stored payload faster during
healthy live operation. Target rates are attempted-byte caps, not guaranteed
backend acceptance. At 10 MB/min, 23.9 GB would take about 40 hours before new
archival, duplicates, pauses and request/backend limitations.

Changes
- Replaces archive_transport.py and overflow_recovery.py; adds recovery_budget.py.
- Starts archive budget at 2 decimal MB/min; increases by 0.5 MB/min per healthy
  minute up to 10 MB/min. One dedicated archive connection/request at a time.
- Archive requests capped at 10 messages / 262144 logical payload bytes, matching
  the currently agreed live batch limits. Only these TWO fields in the main
  central_sync.json are changed. Existing wrapper overhead behavior is retained.
- Moderate live pressure (>25 messages or >30s oldest age) reduces archive budget
  to 0.5 MB/min. Hard limits: >100 live messages or >75s age. Sustained queue
  growth also yields. Two fresh observations required before starting.
- Live transport failures, stale status, manual pause, low memory and high load
  stop archive sends. New live request failures impose 30s cooldown and halve
  target rate (floor 0.25 MB/min). Archive 429 honors Retry-After; network/5xx
  errors retain cooldown. Paused time cannot accumulate a large burst.
- Charge every actual HTTP attempt against the byte budget, including retries
  and split requests. Existing message IDs, payloads, ACK and per-message retry
  decisions remain unchanged. Individual HTTP 200 message rejection no longer
  creates a global archive cooldown; recent rejection details are exposed.
- Recovery status now includes archive HTTP duration totals/max/last and last
  request bytes, to distinguish scheduling limits from slow request turnaround.
- Archive DB opening is unchanged. Do not expect a 10 MB/min cap to overcome
  a slow backend or weak connection; actual capacity must be measured.

21 local tests passed, covering rate limits, live protection, real SQLite partial
ACK/retry behavior, installer guards and exact two-field configuration edits.

INSTALL
cd /root/kalpa-archive-budget-v2
/opt/ornate-ems-gateway/venv/bin/python -B install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python -B install.py
/opt/ornate-ems-gateway/venv/bin/python -B verify.py --duration 300

Installation briefly restarts ONLY Central Sync. It does not restart the main
gateway, stop existing captures, delete queue/archive data or change generation
cadence/live uploader settings. Existing captures record a new service/counter
segment at this restart; compare pre/post separately.

Backups include original source and private central_sync.json under a root-only
/root/kalpa-archive-budget-v2-backup-<UTC>/ directory. Save the printed path.
Rollback restores source and configuration; refuses to overwrite later edits:
/opt/ornate-ems-gateway/venv/bin/python -B install.py --rollback BACKUP_PATH

Optional operator budget overrides: /etc/ornate-ems-gateway/archive_recovery_budget.json
No such file is created by this installer. Absent file uses the tested defaults.
Use this v2 package INSTEAD OF the held v1 package; do not stack both installs.

Field acceptance criteria: archive accepted bytes increase sufficiently to
exceed new archival while live count/age and failures remain acceptable.
Verification collects metadata and CPU without scanning all archive DBs.
