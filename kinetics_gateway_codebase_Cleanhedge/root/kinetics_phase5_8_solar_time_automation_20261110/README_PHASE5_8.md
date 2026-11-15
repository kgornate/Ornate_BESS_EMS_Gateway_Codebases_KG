# Phase 5.8 — Solar-qualified time automation

This cumulative patch retains the validated Phase 5.7.2.2 gateway/control baseline and adds a new independent automation supervisor plus a Flutter Automation page.

## Behaviour

- Per-pair charge and discharge power.
- Daily charge/discharge windows in Asia/Kolkata time.
- Start/resume only after fresh solar remains above 100 kW for 60 seconds.
- Safe Stop All after solar is below 80 kW, offline, invalid or stale for 30 seconds.
- Requalifies and runs the complete automatic sequence if solar returns while the window remains open.
- Configuration changes while active first return all pairs to a verified safe baseline.
- Flutter may be closed; the systemd supervisor owns execution.
- The scheduler reads only the bounded canonical control-view and compact control-status endpoints.

## Installation

Perform Safe Stop All and verify all pairs stopped, then:

```bash
bash verify_phase5_8_solar_time_automation.sh
CONFIRM_IDLE=YES bash install_phase5_8_solar_time_automation.sh
```

Automation installs disabled. Apply the Flutter payload, open **Automation**, configure, save, then enable.
