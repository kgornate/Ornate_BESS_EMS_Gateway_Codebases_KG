# Kalpa ACREL Central Sync patch v1

Installs two agreed S2 changes on the gateway that already has the 2026-10-27 conclusions patch:

- Rename `utility_meter` to `bess_aux_consumption_meter` (BESS Auxiliary Consumption Meter).
- Add `combiner_box_meter` (Combiner Box Meter) for the existing ACREL APM520 integration.

The old utility-meter slot has zero mapped signals in the supplied frozen Kalpa manifest. Renaming it does not add auxiliary-meter readings. ACREL is independent and contributes 34 canonical signals / 34 runtime instances. Updated S2 totals are 2,506 canonical signals / 9,529 expected runtime instances with four BMS/PCS pairs and one enabled meter.

## Recording cadence

| ACREL fields | Count | Recording interval | Backend retention |
|---|---:|---:|---|
| Total active power | 1 | 5 seconds | T2_1Y, one year |
| Other live measurements | 26 | 30 seconds | T2_1Y, one year |
| Energy counters | 7 | 60 seconds | T3_3Y, three years |

All are PERIODIC. Cadences selected for this patch are listed per field in `Kalpa_ACREL_S2_Cadence_v1.xlsx`, using the master workbook's 33-column field-ledger layout. The seven energy counters are total/forward/reverse active energy, total/forward/reverse reactive energy, and total apparent energy. These cumulative values overlap; they are not seven additive energy categories.

This patch reads `meters.acrel_meter_1.telemetry` from the existing authenticated `/api/telemetry/snapshot`. It adds no Modbus traffic and changes no meter wiring, register writes, polling configuration, CT/PT scaling or main gateway service code. It forwards units, quality and each field's source timestamp; energy timestamps do not get replaced by newer measurement timestamps. Bad/stale values stay marked. Source mode, scaling-validation and online state are included. Missing/disabled/never-polled meters produce no invented data.

Meter source polling remains whatever is configured on the gateway (driver defaults: live measurements 5s, energy 60s). The workbook's cadence assumes those defaults. Slower source polls can produce repeated cache values with the original source timestamps. Changing Excel alone does not reconfigure the gateway.

Existing BESS S1–S10 behavior, S2 cadence/checkpoints, overall SoC 30s, and the twelve BMS energy/capacity three-year labels remain unchanged. Old queued envelopes are not rewritten. The optional installed terminal dashboard is updated to show both new substream names; its launcher stays the same.

## Copy from Windows PowerShell

Download `kalpa-acrel-s2-v1.tar.gz` into Downloads:

```powershell
scp "$env:USERPROFILE\Downloads\kalpa-acrel-s2-v1.tar.gz" gw3-ssh.unityess:/root/
ssh gw3-ssh.unityess
```

## Install on DEBIX

```bash
systemctl stop ornate-central-sync.service
cd /root
tar -xzf kalpa-acrel-s2-v1.tar.gz
cd kalpa-acrel-s2-v1
/opt/ornate-ems-gateway/venv/bin/python install.py --check-only
/opt/ornate-ems-gateway/venv/bin/python install.py
/opt/ornate-ems-gateway/venv/bin/python verify_patch.py
```

Central Sync remains stopped. No dependency download, cloud access or main gateway restart is needed. Preflight checks imports, installed source hashes, current frozen manifest checksum, new catalog alignment with the existing ACREL driver, and candidate configuration before mutation. Unknown source/dashboard revisions are rejected for reconciliation. The installer changes two Central Sync source files, adds one catalog and one manifest, edits only the active manifest path/checksum, and updates the existing dashboard when present. Base URL, endpoint, credentials, other configuration and outbox remain unchanged.

The protected timestamped `/root/kalpa-acrel-s2-backup-*` contains originals and a copied installer. The exact guarded rollback command is printed at installation. Repeated installation is a no-op. If files or configuration are edited afterward, rollback refuses to overwrite those later changes.

Optional existing live meter check (prompts for gateway API password):

```bash
/opt/ornate-ems-gateway/venv/bin/python verify_patch.py --check-meter
```

This reads existing API cache only and does not start Central Sync. A cache check does not certify physical CT/PT scaling or cloud delivery.

## When the backend is ready

Backend must accept S2 `bess_aux_consumption_meter` and `combiner_box_meter`, the new `meter.measurement.*` and `meter.energy.*` point IDs, and configured retention labels. ACREL records use `vendor: acrel`, `source_id: acrel_source`, `runtime_asset_id: acrel_meter_1`, and `runtime_signal_id: acrel_source.acrel_meter_1.<key>`. Existing logical/transport envelopes and ACK handling are preserved. Meter records add `source_mode`, `scaling_validated`, `meter_online` fields to the general-asset record.

Resume only when you choose:

```bash
systemctl start ornate-central-sync.service
kalpa-sync-dashboard
```

The already integrated meter must be enabled and supplying cached readings. Starting the service also starts normal producers and uploader; it does not switch mock BESS mode to hardware. Physical gateway installation, meter cache availability and backend ACK acceptance must be verified separately from the included offline tests. Keep it stopped until needed to avoid another mock backlog.
