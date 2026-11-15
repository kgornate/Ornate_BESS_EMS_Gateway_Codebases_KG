# Kinetics Gateway Stage 3C Hotfix

## Purpose

Stage 3C changes only the **active runtime safety-monitor data path**. It does not change the validated BMS/PCS write sequence, power sign convention, ramping, start/stop commands, or safe-stop procedure.

Before Stage 3C, every active pair monitor called a complete direct `fresh=True` status read approximately every two seconds. Four active pairs could therefore repeat many BMS and PCS transactions on top of the normal background polling.

After Stage 3C:

- Runtime monitors consume the existing timestamped background BMS/PCS cache.
- BMS fast, BMS normal and PCS cache freshness are checked independently.
- Missing, offline, failed or stale cache data is never silently accepted.
- A cache-quality failure triggers one immediate direct live confirmation through the Stage 3B single-flight lane.
- If that confirmation is unavailable or fails, the existing exception path performs a fail-safe stop.
- Normal control-sequence direct reads used during start, verification, ramping and safe-stop remain unchanged.

## Files changed

- `app/services/bms_pcs_control.py`
- `app/services/gateway_service.py`
- `app/api/routes.py`

## New read-only diagnostics

- `GET /api/diagnostics/runtime-monitor`
- `GET /api/diagnostics/runtime-monitor/{pair_id}/sample`

The sample endpoint reads only the current gateway cache. It performs no Modbus transaction and no control write.

## Safety behavior

The installer first performs a sequential live verification that all four pairs are at the safe baseline:

- BMS precharge state `0`
- positive and negative contactors open
- PCS operating state `1`
- PCS setpoint approximately `0 kW`
- PCS actual power approximately `0 kW`
- no live-read errors

The installer aborts before modifying files if any pair is not safe.

## Installation

```sh
cd /root/kinetics_stage3c_hotfix_20260728
sh ./install_stage3c_on_gateway.sh
```

Keep Flutter closed and do not send control commands during installation.

## Cache-only verification

```sh
cd /root/kinetics_stage3c_hotfix_20260728
nohup sh ./verify_stage3c.sh > /root/stage3c_verify.log 2>&1 &
tail -f /root/stage3c_verify.log
```

The verification:

1. Samples the runtime-monitor cache for all four pairs.
2. Compares cached critical safety fields against one sequential live read per pair.
3. Runs a five-minute four-pair cache-sampling and memory test.
4. Confirms the cache-only stress did not initiate direct live refreshes.

This verification does not energize any pair. Active runtime-monitor validation must be done afterward with one pair at low power before multi-pair operation.

## Rollback

The installer prints the exact backup path and rollback command. It automatically attempts rollback if installation, import, service startup, health, or cache-sample validation fails.
