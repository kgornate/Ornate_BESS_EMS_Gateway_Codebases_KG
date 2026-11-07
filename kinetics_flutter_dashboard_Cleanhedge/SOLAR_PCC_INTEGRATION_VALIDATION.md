# Solar / PCC integration validation

## Gateway backend

- Asset ID: `solar_plant`
- Hopewind source: `172.16.0.111:502`, Unit ID 1
- ABB source: `172.16.0.113:502`, Unit ID 1
- Background polling class: `solar`
- REST cache: `/api/telemetry/compact` under `solar`
- Live updates: `/ws/telemetry` as `telemetry_update` assets
- Historian: `solar_plant` is included in periodic compressed SQLite batches
- Diagnostics: both Hopewind and ABB I/O-arbiter links are exposed in health
- Alarm processing: `solar_plant` participates in the common asset alarm path

## Flutter dashboard

- Compact snapshot parsing and WebSocket delta merge
- Solar / PCC navigation destination
- Overview generation/import/export cards and solar asset card
- Dedicated Hopewind and ABB telemetry sections
- Searchable all-signals engineering table
- Online/offline, timestamp and quality presentation
- Existing historian asset selector automatically includes `solar_plant`

## Local validation commands

```powershell
flutter pub get
flutter analyze
flutter test
flutter build windows --release
```

The integration is read-only. Enabling automatic solar-following dispatch is a
separate controlled field step and is not performed by this Flutter release.
