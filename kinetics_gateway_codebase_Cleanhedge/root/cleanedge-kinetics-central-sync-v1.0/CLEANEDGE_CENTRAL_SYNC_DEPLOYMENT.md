# CleanEdge Kinetics Central Sync v1.0

This package ports the deployed Kalpa Central Sync transport/reliability architecture to the **CleanEdge Kinetics** gateway while keeping CleanEdge asset identities, polling, Modbus ownership, automation, interlocks, APIs and HMI unchanged.

## Integration map

| Area | Result |
|---|---|
| Reused from Kalpa | Durable SQLite outbox and sequences; per-message ACK/duplicate/reject/missing-ACK handling; immediate ACKed-payload release with compact receipts; logical S2 chunking; durable S2 checkpoints; retry/batching; overflow archival and verified recovery; dedicated archive HTTP client; adaptive v2.1 pressure protection; v2.2 reuse/timing instrumentation; S1–S10 producer framework; dashboard and continuous observer. |
| CleanEdge adapters | Cached telemetry normalization for S1; CleanEdge Kinetics BMS/PCS catalogs; four rack/PCS instances; BMS cell-array expansion; BESS auxiliary meter; ABB grid meter; HopeComBox/S2.11 cache aliases; CleanEdge health/control APIs; Kinetics systemd paths and service names. |
| Contract preserved | Existing stream/substream names, message envelope, S2 record fields, backend enum/wire formats, stable IDs/sequences and ingest/authentication behaviour. Canonical point IDs remain workbook-defined even when runtime keys differ. |
| Deliberately disabled | Cloud command downlink (D1); S5, S6 and S7 where the corresponding application source is not deployed; every workbook row marked planned, source-absent, driver-extension-required, reserved or NOT_UPLOADED. |
| Site-specific configuration | Site `cleanedge`, gateway `kinetics_gateway_1`, `/opt/kinetics-gateway`, `/etc/kinetics-gateway`, `/var/lib/kinetics-central-sync`, and `/mnt/ems-logs/kinetics-gateway/central_sync`. No Kalpa asset IDs or deployment paths are used. |

The approved S2 manifest contains **1,786 canonical points** and **6,184 expected runtime signal instances**. The complete inclusion/exclusion evidence is in `validation/cleanedge_s2_mapping_coverage.json`.

## Inputs still requiring field confirmation

These do not block installation, but they block claiming production validation:

1. Backend-published maximum request bytes/message count and accepted compression/concurrency. The supplied backend note did not state these limits, so this package retains the deployed Kalpa baseline: one worker, no gzip, 10 messages and 256 KiB per request. Archive recovery also retains Kalpa's verified adaptive 6 MB/min initial and 20 MB/min maximum budget.
2. Final backend-issued CleanEdge client credentials. They are injected through `/etc/kinetics-gateway/central_sync.env`; none are packaged.
3. A short live source/cadence verification, followed by continuous observation. No 24-hour CleanEdge result is claimed in this offline package.

## Install (safe/default disabled)

```sh
tar -xzf cleanedge-kinetics-central-sync-v1.0.tar.gz
cd cleanedge-kinetics-central-sync-v1.0
sha256sum -c SHA256SUMS
./deployment/install_cleanedge_central_sync.sh
```

The installer backs up only Central Sync-related files and does not replace `/etc/kinetics-gateway/config.json` or restart `kinetics-gateway.service`.

Add the backend credentials and the already-deployed internal API credential:

```sh
vi /etc/kinetics-gateway/central_sync.env
chmod 600 /etc/kinetics-gateway/central_sync.env
```

## Preflight and activation

```sh
cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m app.central_sync.service \
  --config /etc/kinetics-gateway/central_sync.json --dry-contract

activate-cleanedge-central-sync
```

## Short field verification

```sh
systemctl is-active kinetics-gateway.service kinetics-central-sync.service
journalctl -u kinetics-central-sync.service -n 100 --no-pager
kinetics-delivery-dashboard --once

cd /opt/kinetics-gateway/backend
/opt/kinetics-gateway/venv/bin/python -m app.central_sync.service \
  --config /etc/kinetics-gateway/central_sync.json --status
sqlite3 /var/lib/kinetics-central-sync/central_sync.db \
  "select stream,substream,state,count(*) from outbox_messages group by 1,2,3 order by 1,2,3;"
```

Verify backend evidence using accepted/already-processed receipt counters—not HTTP 2xx alone—and compare measured delivery with configured cadence rather than treating them as the same metric.

## Continuous observation

```sh
cd /opt/kinetics-gateway/backend/tools/continuous_observer
./run.sh
journalctl -u cleanedge-central-sync-observer -f -o cat
```

This observer is read-only, bounded, and service-restart-aware. It measures live delivery and archive drainage together. An interrupted or incomplete run remains evidence with explicit coverage gaps; it is not reported as passed.

## Rollback

```sh
disable-cleanedge-central-sync
./deployment/rollback_cleanedge_central_sync.sh
```

The rollback path restores the installer-created Central Sync backup and leaves the existing CleanEdge polling/control service untouched.
