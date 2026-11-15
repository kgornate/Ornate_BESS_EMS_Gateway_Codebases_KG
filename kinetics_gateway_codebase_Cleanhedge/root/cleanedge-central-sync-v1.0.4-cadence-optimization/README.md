# CleanEdge Central Sync v1.0.4 — Cadence Optimization

This is a cadence-only patch. It retains all 1,786 canonical S2 points and
6,184 runtime signals and does not alter stream/substream names, point IDs,
asset mappings, units, record fields, envelopes, authentication, backend URLs,
outbox contents, archive contents, or gateway control behavior.

## Approved focused change

| CleanEdge category | Previous | Optimized | Kalpa category basis |
|---|---:|---:|---|
| BMS cell voltage array | PERIODIC 30 s | PERIODIC 120 s | BMS_CELL_V |
| BMS cell temperature array | PERIODIC 30 s | PERIODIC 180 s | BMS_CELL_T |

The change covers 416 canonical voltage definitions and 416 canonical
temperature definitions. With four runtime instances each, it corrects 3,328
runtime signal cadences. No signal is excluded.

The frozen-manifest model drops from 8,518.8 to 3,249.5 scheduled S2 records
per minute (61.9%). Actual message and byte reduction must be measured because
chunking, changes, and payload sizes are runtime-dependent. Based on the field
measurement, BMS traffic should fall materially from the previous 7.25 MB/min.

## Install

```sh
cd /root/cleanedge-central-sync-v1.0.4-cadence-optimization
sha256sum -c SHA256SUMS
./deployment/install.sh
```

The restart causes one intentional `policy_change` emission for the changed
points. Judge steady state after five minutes, then measure for three minutes:

```sh
/opt/kinetics-gateway/venv/bin/python \
  /root/cleanedge-central-sync-v1.0.4-cadence-optimization/tools/measure_generation.py \
  --seconds 180
```

Also check:

```sh
systemctl --no-pager --full status kinetics-central-sync.service
timeout 20s /usr/local/bin/kinetics-delivery-dashboard
```

The running 72-hour test remains valid but must be analyzed as two segments:
before and after the v1.0.4 service restart/policy change.

## Rollback

```sh
/root/cleanedge-central-sync-v1.0.4-cadence-optimization/deployment/rollback.sh
```
