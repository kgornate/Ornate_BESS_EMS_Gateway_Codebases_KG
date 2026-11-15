# CleanEdge Kinetics Central Sync v1.0.1 Correctness Patch

This focused adapter-layer patch preserves the established stream names,
message envelope and record structures while correcting CleanEdge runtime
interpretation.

Corrections:

- S1 rack SOC/SOH: per-mille to percent.
- S1 cell voltage: mV to V.
- S1 insulation: kOhm to MOhm.
- S1 rack charge/discharge limit key aliases.
- S1 explicit engineering-unit metadata.
- S2 rack SOC-family conversion to percent.
- S2 auxiliary energy-meter IEEE-754 decoding and frozen-policy routing.
- S2 canonical bank de-duplication (one system bank; racks remain per-pair).
- S10 adaptation to CleanEdge's nested compact pair schema.
- Shared authenticated local API client, bounded snapshot cache and transient
  HTTP 503 retries to reduce overlapping full-snapshot requests.

No Modbus reads/writes, automation logic, safety interlocks, backend fields,
credentials, command policy or cadence manifest are changed.

Install:

```sh
cd /root/cleanedge-central-sync-v1.0.1-correctness-patch
sha256sum -c SHA256SUMS
./deployment/install.sh
```

The installer preserves existing SQLite outbox and SD archives. Consequently,
old queued messages retain their original values; only newly generated
messages use corrected engineering values.

Rollback:

```sh
systemctl stop kinetics-central-sync.service
cp -a /root/cleanedge-central-sync-v1.0.1-backup-*/\*.py \
  /opt/kinetics-gateway/backend/app/central_sync/
systemctl start kinetics-central-sync.service
```

