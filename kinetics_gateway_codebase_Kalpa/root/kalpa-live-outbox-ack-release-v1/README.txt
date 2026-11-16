KALPA live sending queue: release valid ACKed payloads immediately

PROBLEM CONFIRMED ON GATEWAY: 24,422 ACKed rows / 980.24 MiB JSON filled the
1 GiB logical live DB allowance. There were no unacknowledged live rows;
archival could not move delivered history. 24-hour ACK retention blocked
fresh enqueueing despite free SD and internal disk capacity.

INSTALL (gateway venv Python):
  python install.py --check-only
  python install.py
  python verify_gateway.py
  kalpa-delivery-dashboard

Installer backs up source/config/viewer files, briefly stops Central Sync if
active, applies a guarded additive outbox patch and removes existing rows
ONLY when state='acked', ack_status is accepted/already_processed and ACK
timestamp exists. Unknown/unverified ACK rows remain. Pending, retry,
inflight and dead rows are hashed before/after migration in the same SQLite
transaction, together with sequences, S2 checkpoints and generation metrics.
Mismatch aborts/rolls back migration. SD archives are never edited by installer.
No delivered payload backup is made: their removal is intentional. The source
and config backup does NOT restore deleted ACKed payload history.

New valid per-message ACK processing writes a compact operational receipt
and deletes that message payload from outbox_messages IN ONE TRANSACTION.
Receipt/deletion failure rolls back the transaction, leaving payload queued
for at-least-once retry. An HTTP 200 alone is insufficient; the existing
uploader must first classify that message as accepted/already_processed.
Missing ACKs, retryable/permanent rejection handling, timeouts and retries
retain existing behaviour. No cadence, logical message size, batch limit,
timeout, TLS/auth, priority, concurrency or archive/recovery policy changes.
The legacy single-message mark_accepted method uses the same transaction.

The live outbox is a sending queue, not a historian. Compact dashboard
receipts have no telemetry values or payload JSON; fields are message ID,
stream/substream, ACK status/time, record count, byte count, creation time and
attempt count. Bounded to 5 minutes AND at most 5,000 entries, pruned during
ACK transactions and cleanup. They share SQLite solely for atomic receipt +
payload deletion, and occupy small bounded operational space. They are not
a 24-hour delivered-data archive. A cap-truncated minute window is labelled
unavailable, not silently claimed complete. Backend storage remains the
delivered data destination; the proposed separate SD historian is not added.

Configuration acked_retention_hours becomes 0 and cleanup_interval_sec 60.
New ACK payload deletion is immediate and independent of periodic cleanup.
Dead letter and transport attempt retention remain unchanged. Their storage,
and genuinely unsent data, can still occupy capacity; no infinite-storage
guarantee is introduced. The 1 GiB allowance is unchanged.

The main delivery and upload dashboards are adapted to compact receipts for
ACK/min and recent accepted IDs. S2 detail labels its ACK count as last 5min;
current pending/retry/inflight counts remain queue states. Producer-based
generation MB/min stays intact. Older kalpa-sync-dashboard retained-row
generation estimates can undercount when sent rows disappear; use the main
kalpa-delivery-dashboard for generation and delivery comparison. Historical
test ACK scanners reading state='acked' payloads are obsolete; do not use
those old scanners for subsequent acceptance measurements.

SQLite allocated FILE SIZE may remain near 1 GiB. Deleted pages become
reusable, and existing capacity logic subtracts freelist pages. No VACUUM,
file deletion, database recreation or cap increase is needed. Installer
reports logical used/reusable space and checkpoints WAL with service stopped.

Installation refuses while known measurement services remain active. Active
Central Sync is restarted afterward; an initially inactive service remains
inactive. Failure restores source/config/viewers. If migration already
committed before a later startup failure, removed ACK payloads stay removed.
Rollback: python install.py --rollback /root/kalpa-ack-release-backup-UTC
Rollback restores code/config only, not ACKed data. Preserve unsent queues.

This fixes the confirmed delivered-history capacity blockage. It does NOT
fix backend S2 storage/claim deferrals, ACREL availability or S4 enum mismatch.
Successful verification is local generation/storage recovery, not proof that
backend acceptance now matches generation.

Offline tests exercise the actual outbox methods with a fake message type,
the real uploader missing-ACK parser, transaction rollback, protected states,
logical-capacity recovery, metric bounds and viewer compatibility. Original
source fixtures are taken from the workspace backup or installed gateway.
Run with gateway venv: python -m unittest -v test_release.py
