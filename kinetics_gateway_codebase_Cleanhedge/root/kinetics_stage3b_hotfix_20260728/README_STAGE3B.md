# Kinetics Gateway Stage 3B Hotfix

Purpose: prevent overlapping control-status live reads from creating an unbounded FastAPI/Modbus request queue and another Uvicorn OOM.

Changes:

- `/api/control-sequence/{pair_id}/status` now defaults to `fresh=false`.
- `fresh=false` returns only the in-memory automatic-run state and performs no direct BMS/PCS reads.
- `fresh=true` remains available for one-time engineering verification.
- Exactly one live status refresh is allowed per pair.
- Exactly one live status refresh is allowed globally across all pairs.
- Duplicate HTTP `fresh=true` calls never wait in a long queue.
- If a prior live result exists, duplicate calls return it with `refresh.source=cached_while_live_refresh_busy`.
- If no prior live result exists, the API returns HTTP 409 `live_status_busy`.
- Internal control and safety operations use a bounded wait for the single live-read lane.
- Automatic-start and capabilities responses now advertise `fresh=false` for normal polling.
- New diagnostic endpoint: `/api/diagnostics/control-refresh`.

Unchanged:

- BMS precharge/register writes.
- PCS configuration/start/stop/setpoint writes.
- Charge/discharge sign convention.
- Power ramping, safe-stop and validated control sequence.
- Stage 3A historian/storage fixes.

Stage 3B does not yet replace the runtime safety monitor's direct live reads. That optimization is Stage 3C after this API single-flight patch is verified.
