# Phase 5.8.3 operator timeline and RTC readiness

This package adds a paginated persisted automation-event API and Flutter UI
with a scrollable/filterable timeline, explicit date/time formatting, live
gateway-clock elapsed time, and next-window countdown.

The RTC installer is intentionally opt-in. Run `bash detect_rtc.sh` on the
gateway first. Then pass the confirmed external device, for example:

```sh
CONFIRM_IDLE=YES RTC_DEVICE=/dev/rtc1 bash install_phase5_8_3_gateway.sh
```

Without `RTC_DEVICE`, only the event-history API is installed. The RTC logic
validates the calendar year before restoring the system clock, seeds the RTC
from valid system time at installation, updates it hourly, and lets automation
start from a validated RTC when NTP/Internet is unavailable. If RTC validation
fails, the existing NTP/external-time guard remains the fallback.

Copy `flutter/` to the Windows Flutter source tree and run
`build_windows_release.ps1`. The two changed Flutter files are:

- `lib/core/state/gateway_controller.dart`
- `lib/screens/automation_screen.dart`
