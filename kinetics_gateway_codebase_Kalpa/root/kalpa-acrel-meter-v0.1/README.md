# Kalpa ACREL APM520 integration v0.1

Read-only Modbus RTU meter integration for the Kalpa backend on DEBIX i.MX93.
Baseline: KALPA_DEBIX_MIGRATION_20261019_085354.tar.gz. The installer applies
contextual edits and preserves unrelated changes, including separate connectivity
modules. It aborts before deployment if those edits cannot be applied cleanly.

## Confirmed on your gateway before this update

- RS485-1 is `/dev/ttyLP6`; meter slave 1, 9600 baud, 8N1, FC03.
- DEBIX A1+ connects to meter terminal 21/A; B1- to terminal 22/B.
- Both `RS485_nRE1` and `RS485_nDE1` brightness values were 1.
- A 54-register read at 0x2000 succeeded with valid CRC. No-load electrical
  measurements were zero and power factor was 1.0 in high-word-first order.
- Loaded electrical scaling, power direction, and energy counters have not yet
  been commissioned against the physical display.

## Included behavior

- 27 electrical points from 0x2000 through 0x2034, float32, polled every 5 seconds.
- 7 primary-side float32 energy counters from 0x3080 through 0x308C, polled every
  60 seconds. These are NOT the secondary-side Uint32 counters at 0x3000.
- No extra CT/PT multiplication. Configure meter ratios correctly and compare
  readings with its display. Forward/reverse are not relabeled import/export
  until physical placement and current direction are confirmed.
- Asset ID `acrel_meter_1`; location `unassigned`; scaling_validated false.
- Meter mode independently selects hardware or mock. Existing mock BMS/PCS can
  continue while the actual meter is polled. Hardware errors never produce
  simulated replacements.
- Polling starts independently of BMS/PCS warm-up. Serial timeouts do not block
  HTTP cache reads. Port reconnection uses the existing RTU client's retry logic.
- Unavailable/nonfinite/stale data has explicit status and quality; retained
  values keep their original observation timestamps. Measurement and energy
  points carry individual timestamps because their intervals differ.
- Existing gateway historian/SQLite compression, quota and retention apply. A
  meter history record stores status, source and individual point timestamps.
  History cadence uses existing storage settings; fallback storage often samples
  every 60 seconds. The meter does not override those limits.
- Authenticated API, asset list, full/compact snapshots, telemetry delta updates,
  polling diagnostics and existing historian/CSV export include the meter.
- No meter writes or control actions. Generic control requests for its ID are
  explicitly rejected. No BMS/PCS control or safety logic consumes meter values.
- This release does not add an HMI meter panel or change the frozen Central Sync
  signal policy. Central Sync activation and meter stream mapping are separate.

## Install on DEBIX

Prefer wired Ethernet SSH during installation. Save the downloaded tar.gz in
Windows Downloads. In PowerShell:

```powershell
scp -O "$HOME\Downloads\kalpa-acrel-meter-v0.1.tar.gz" root@192.168.10.2:/root/
ssh root@192.168.10.2
```

On DEBIX:

```bash
cd /root
tar -xzf kalpa-acrel-meter-v0.1.tar.gz
/opt/ornate-ems-gateway/venv/bin/python /root/kalpa-acrel-meter-v0.1/install.py --enable-meter
```

The installer verifies package hashes, stages/compiles changes and validates the
merged configuration using the gateway venv BEFORE stopping the application.
It backs up modified files and config under `/root/kalpa-acrel-backup-*`, briefly
restarts ONLY `ornate-ems-gateway`, and restores files automatically if API startup
fails. Network/WPA/Cloudflare, service unit definitions, Central Sync, databases,
credentials, existing BMS/PCS configuration and Python dependencies are not
replaced. The only config addition is the `meter` section. Do not interrupt power
during application installation. Use `--check-only --enable-meter` for preflight
without deployment. Running without `--enable-meter` adds the feature disabled.

If a conflict is reported, do not copy archived files over the current backend.
Share the named current source file so its changes can be reconciled.

## Verify on the real gateway

```bash
systemctl status ornate-ems-gateway --no-pager -l
/opt/ornate-ems-gateway/venv/bin/python /root/kalpa-acrel-meter-v0.1/verify_meter.py --wait-history
```

Enter the existing Kalpa internal API password when prompted (not the Wi-Fi
password). No password is stored by the verification script. It prints the live
meter asset, checks API exposure and waits up to 150 seconds for online history.
If your configured historian interval is longer, verify again after that interval.
A successful API start alone does not prove live meter communication; confirm
`source=hardware`, `online=true`, point quality and timestamps. Check energy
errors separately: measurement success can coexist with failed energy reads.

After this works, reboot and rerun verification. Compare phase voltage, current,
signed total active power, frequency and forward/reverse energy against the meter
display under known inputs before setting `scaling_validated=true`.

## API (existing Bearer authentication)

| GET endpoint | Purpose |
| --- | --- |
| `/api/meters` | Meter collection and count |
| `/api/meters/acrel_meter_1` | Full asset, status, errors and telemetry |
| `/api/meters/config` | Internal-user-only settings |
| `/api/meters/acrel_meter_1/history?limit=100` | Persisted meter history |
| `/api/historian/acrel_meter_1?limit=100` | Existing generic history endpoint |
| `/api/export/historian/acrel_meter_1.csv` | Existing generic CSV export |
| `/api/telemetry/snapshot` | New `meters` collection alongside BMS/PCS |
| `/api/telemetry/compact` | Compact meter points with v/q/u |
| `/api/diagnostics/polling` | New meter poll counters |

History endpoints accept existing `since` ISO-8601 filtering and bounded limits
1..5000. Customer-role users can read meter data but cannot change/control it.
The initial energy read may fail on a different firmware/map; this is exposed as
`degraded`, not replaced with zero. If confirmed unsupported, set energy_enabled
false while retaining electrical polling.

## Configuration

Settings live in `/etc/ornate-ems-gateway/config.json`, top-level `meter` section.
Edit that section and restart `ornate-ems-gateway` to apply. Settings include:

```json
{
  "enabled": true,
  "asset_id": "acrel_meter_1",
  "label": "ACREL APM520",
  "location": "unassigned",
  "mode": "hardware",
  "device": "/dev/ttyLP6",
  "unit_id": 1,
  "baudrate": 9600,
  "parity": "N",
  "stopbits": 1,
  "word_order": "big",
  "timeout_seconds": 2,
  "retries": 1,
  "poll_seconds": 5,
  "stale_after_seconds": 20,
  "energy_enabled": true,
  "energy_poll_seconds": 60,
  "scaling_validated": false
}
```

The enabled meter cannot independently share the configured PCS RTU port; a
shared bus would require a common bus owner. The current Kalpa PCS use TCP, so
this does not affect them. `/dev/ttyLP0` is rejected as the DEBIX console.
Exclusive serial opening prevents another cooperating pyserial owner from
taking the same port; it is not a substitute for checking other applications.

## Roll back

Use the exact backup directory printed by the installer:

```bash
/opt/ornate-ems-gateway/venv/bin/python /root/kalpa-acrel-backup-TIMESTAMP/rollback.py
```

Rollback refuses to overwrite files changed since installation. It restores the
previous source and config and restarts the application. Meter history already
stored in SQLite is retained. Backups contain the existing configuration: keep
them private and do not upload them without removing secrets.

## Validation boundaries

See VALIDATION.txt for test counts and exact local runtime versions. Tests use
isolated temporary stores and a simulated RTU endpoint; the physical meter was
not accessible from the development environment. Your previous direct serial
probe is the physical-communication evidence. Continuous DEBIX polling, energy
counter support and reboot persistence must be checked after this installation.
