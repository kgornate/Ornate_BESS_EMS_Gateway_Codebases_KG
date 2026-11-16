# Kalpa Central Sync agreed-conclusions patch v1

Prepared against the DEBIX gateway export from 2026-10-26. Install on DEBIX as root using the existing gateway Python environment. This archive contains no site credentials.

## Changes

1. Active uploader configuration and code defaults use `/api/v1/gateway/ingest`. The existing base URL, client ID/secret, token route, transport envelope, gzip, retry and ACK handling are preserved.
2. Exactly twelve Lineage energy/capacity canonical fields (six bank + six rack fields; 30 runtime instances for four racks) emit `retention_class: T3_3Y`. These are charge/discharge session energy, full charge/discharge capacity, and accumulated charge/discharge energy at bank and rack level. This is backend history metadata. Local outbox cleanup/retention is unchanged. Backend must implement the agreed three-year history policy.
3. Add `bank.system_data.system_soc` from the existing BMS bank reading, as `system_soc` in S2 `ems_system`: PERIODIC every 30 seconds, unit %, one instance, normal `T2_1Y` retention. The value is not calculated from rack SoCs. A source value/quality is forwarded as supplied; the patch does not invent a system SoC when the local source omits it.

The versioned v1.1 S2 manifest has 2,472 canonical points and 9,495 expected runtime instances for the four-pair configuration. The existing v1.0 manifest remains available. Every existing signal keeps the same recording interval, mode and checkpoint revision. Only the new SoC requires a first emission. No ACREL/utility-meter forwarding is added in this patch. No command/control behavior or Modbus poll cadence changes.

## Install

Download `kalpa-central-sync-conclusions-v1.tar.gz`, transfer it to `/root` on DEBIX, then run:

```bash
cd /root
tar -xzf kalpa-central-sync-conclusions-v1.tar.gz
cd kalpa-central-sync-conclusions-v1
/opt/ornate-ems-gateway/venv/bin/python install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python install.py
```

The installer validates staged imports/configuration and manifest checksum before stopping Central Sync. It rejects reviewed source files that differ from both the export and this patch. If rejected, supply the named current source file for reconciliation; do not force overwrite it. Other integration changes outside these five source files are preserved.

Installation creates a protected timestamped backup under `/root/kalpa-sync-conclusions-backup-*`. It changes only five Central Sync source files, adds the v1.1 manifest, and edits three active configuration fields: `backend.ingest_path`, `general_assets.s2_policy_manifest_path`, `general_assets.s2_policy_expected_sha256`. It restarts Central Sync only if it was active, leaves boot enablement unchanged, and does not restart the main gateway service. It does not edit the outbox, saved checkpoints, environment/credentials or archive data.

If Central Sync was inactive and you intend to run it, start it separately:

```bash
systemctl start ornate-central-sync.service
```

Internet is unnecessary for installing the patch or generating local telemetry. Remote backend delivery requires connectivity and a ready authenticated backend. New URL/client credentials can be configured separately when supplied by the backend team.

## Verify after at least 60 seconds

```bash
cd /root/kalpa-central-sync-conclusions-v1
/opt/ornate-ems-gateway/venv/bin/python verify_gateway.py
journalctl -u ornate-central-sync.service --since '3 minutes ago' --no-pager -o cat | tail -n 60
kalpa-sync-dashboard
```

`verify_gateway.py` opens the live outbox read-only, checks installed hashes and configuration, and inspects at most 1,000 recent retained S2 messages. It prints no tokens or configuration secrets. It does not start another producer. A missing retained sample requires investigation; records may already have left the database. Dashboard rates alone cannot certify cadence. This patch was validated offline; installation on the physical DEBIX and successful backend acceptance remain to be verified.

The new labels apply to newly generated records. Already queued messages retain their old labels and message IDs. Some unchanged full-capacity signals have a 1,800-second heartbeat, so their next new-tag record can take 30 minutes. Coordinate the three-year backend retention policy for pre-existing queued energy records too. Do not rewrite queued JSON or reset checkpoints to accelerate this.

A 200 response alone is insufficient: backend ACK handling must also mark the intended messages as acknowledged. A backend 500/503 does not invalidate local cadence verification. Review delivery separately once the backend endpoint is ready.

## Rollback

Use the exact rollback command printed by the installer. It uses the backup's copied installer, for example:

```bash
/opt/ornate-ems-gateway/venv/bin/python /root/kalpa-sync-conclusions-backup-TIMESTAMP/install.py --rollback /root/kalpa-sync-conclusions-backup-TIMESTAMP
```

Manual rollback refuses to overwrite files/configuration changed since installation, including subsequent credential or URL edits. Installation failure restores applied source/configuration changes automatically and attempts to restore previous service activity. If service startup still fails after restoration, inspect systemd logs. Existing outbox records and checkpoints stay intact, including any new SoC record generated before a manual rollback.

## Validation

See `validation.json` for offline test evidence. Tests cover existing stream contracts, old frozen S2 behavior, all 9,495 instances and bounded messages, exact retention changes, SoC cadence boundaries/restarts, checkpoint-compatible upgrade, source drift rejection, configuration/credential preservation, check-only, repeat installation, active/inactive service behavior, rollback, and restoration after an injected startup failure.
