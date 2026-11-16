KALPA ARCHIVE BUDGET v2.1 - NARROW POLICY REFINEMENT

Requires the exact deployed v2 source. Changes only recovery_budget.py and
overflow_recovery.py. Configuration values and byte-budget/request-size limits
are unchanged. No queue data, schemas, stored messages or ACK logic changed.

Before: a live queue rising by 10 messages over as little as 5s could halve
the archive target even when messages were young and live had zero failures.
After: pressure-triggered halving requires at least the full configured trend
window (default 30s), continuously above BOTH soft count and soft age limits,
with count increasing by >=10 and oldest age worsening by >=10s over that
window. Brief bursts/recovering age do not halve the target. Moderate pressure
still uses the existing limited budget. Hard live count/age, manual pause,
memory/load guards, live transport failures and archive HTTP failure cooldown
remain effective. Byte budgets still account for every actual POST attempt.

Cooldown labels now distinguish live_pressure_cooldown, live_transport_cooldown
and archive_transport_cooldown. A live queue pause is no longer labelled as a
backend cooldown. Scheduler status version: byte_budget_v2_1.

27 local tests include actual recorded five-minute health-trace replay,
brief bursts, jittered persistent growth, recovering age, hard resource guards,
real SQLite ACK/retry protection and installer rollback checks. This validates
policy behavior, not a guaranteed field throughput.

INSTALL
cd /root/kalpa-archive-budget-v2.1
/opt/ornate-ems-gateway/venv/bin/python -B install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python -B install.py
/opt/ornate-ems-gateway/venv/bin/python -B verify.py --duration 300

Installation briefly restarts ONLY Central Sync. Main gateway and existing
captures remain running. This creates another counter segment in the 48-hour
capture; use started_utc to segment comparisons. Backups and rollback command
are printed. Rollback restores the deployed v2 policy; does not revert the
earlier v2 installation or its 10-message/256KiB archive request settings.

This patch does not alter old alarm enum payloads or solve the backend's
per-message store-not-confirmed responses. Those remain separate issues.
