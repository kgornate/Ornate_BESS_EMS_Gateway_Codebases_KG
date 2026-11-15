# Phase 5.5 — Control Cache Isolation

Field evidence showed a successful Pair 1 safe-stop taking 103.7 seconds while
cache-only status requests rose to 5–42 seconds and then returned HTTP 503.
Flutter timed out at 60 seconds and retained the old charging presentation.

This patch:

- publishes a bounded pair-control cache containing only required protocol points;
- keeps `fresh=false` reads independent from the main plant snapshot lock;
- reduces repeated deep-copy work from complete PCS/BMS catalogs;
- extends single-pair Flutter safe-stop/abort timeout to 150 seconds;
- keeps reconciliation polling alive for 210 seconds;
- commits power-tracking completion immediately after the required coherent
  PCS cache samples match;
- publishes the last/recent tracking samples and consecutive-match count while
  the step is running;
- replaces blocking post-match fieldbus sweeps with bounded coherent-cache
  safety diagnostics; the runtime monitor continues protection afterward;
- preserves all control registers, command ordering and safety verification.

Install only while all pairs are stopped:

```sh
CONFIRM_IDLE=YES bash install_phase5_5_control_cache_isolation.sh
```

Apply the Flutter half on Windows from the extracted package:

```powershell
& ".\apply_flutter_phase5_5.ps1" `
  -FlutterRoot "C:\path\to\kinetics_flutter_dashboard_Cleanhedge"
```
