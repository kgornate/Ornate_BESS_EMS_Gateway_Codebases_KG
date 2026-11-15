# Phase 5.2 Control Reliability

Built exactly against the installed Phase 5 v2.1 gateway and Flutter baseline.

## Changes

- Preserves all registers, command values, control steps and safety gates.
- Publishes a lock-independent immutable control cache before alarm/storage work.
- Reserves one HTTP lane exclusively for zero-power, abort and safe-stop.
- Makes Flutter automatic pair polling single-flight.
- Stops tracking traffic before Flutter submits safety commands.
- Replaces unbounded serial `flush()` with calculated UART wire-time waiting.

## Install

Only while every pair is stopped and physically verified safe:

```bash
cd /root/kinetics_phase5_2_control_reliability_20261109
CONFIRM_IDLE=YES bash install_phase5_2_control_reliability.sh
```

Apply `apply_flutter_phase5_2.ps1` separately on Windows, then format, analyze and rebuild Flutter.
