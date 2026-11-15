# Phase 5.4 — Authoritative telemetry and command reconciliation

This patch fixes the final power-tracking stall and the Flutter control page retaining pre-stop values.

- PCS setpoint, actual power and operating state now come from the same coherent background PCS asset snapshot used by the PCS page.
- Stage feedback can enrich BMS transition fields but cannot overwrite authoritative PCS values.
- Actual-power tracking requires a fresh PCS sample produced after tracking began.
- Live scoped reads remain in place for command readback and final BMS safety/power-consistency checks.
- During start, safe-stop and abort, the Flutter pair-specific tracker owns that pair; compact overview polling cannot overwrite it.
- Cross-source timestamps are no longer treated as a single ordering clock.
- Safe-stop/abort apply `final_status` immediately and continue one-second reconciliation, with a 90-second watchdog.

Install only while all pairs are stopped:

```bash
CONFIRM_IDLE=YES bash install_phase5_4_authoritative_reconciliation.sh
```

Apply Flutter from PowerShell, then format and analyze:

```powershell
.\apply_flutter_phase5_4.ps1 -FlutterRoot "C:\path\to\kinetics_flutter_dashboard_Cleanhedge"
cd "C:\path\to\kinetics_flutter_dashboard_Cleanhedge"
dart format lib\core\state\gateway_controller.dart
flutter analyze
```
