# Kalpa Energy Meter HMI v0.1

Requires the ACREL backend v0.1 already installed on DEBIX. This update changes
only the existing local web HMI: index.html, app.js and styles.css. No backend
restart, network change, database migration or extra dependency is needed.

## What appears

- Energy meter tab in the existing sidebar, using the current ORNATE theme.
- Overview summary with active power, frequency, forward energy and last measurement.
- Active/reactive power, power factor and frequency cards.
- Three-phase voltage/current/power/PF table and neutral current/apparent power.
- Seven primary-side energy counters, labeled forward/reverse rather than
  uncommissioned import/export directions.
- Connection status, hardware/simulation source, timestamps, read errors and
  commissioning status.
- Latest 60 historian samples with source and status, automatic refresh while
  viewing the meter and a manual refresh button.

Meter readings refresh every five seconds through authenticated same-origin APIs.
History refreshes every 15 seconds while the meter tab is open; stored sampling
still follows the existing gateway historian interval, often 60 seconds on fallback
storage. Measurement/energy values are shown only when the meter is online and
the point quality is good or explicitly simulated. Failed, stale and unavailable
live values show dashes, never substituted zero. History is clearly historical.
Simulated readings are labeled. Measurement scaling remains pending commissioning.

Meter requests run separately from existing BMS/PCS refreshes and have a ten-second
timeout. A meter API failure does not stop the BMS/PCS screen updating, and a
failure in another dashboard request does not stop the meter page updating.
No meter settings or field-control writes are added to the web HMI.

## Install

Save the archive to Windows Downloads. PowerShell:

```powershell
scp -O "$HOME\Downloads\kalpa-meter-hmi-v0.1.tar.gz" gw3-ssh.unityess:/root/
ssh gw3-ssh.unityess
```

On DEBIX:

```bash
cd /root
tar -xzf kalpa-meter-hmi-v0.1.tar.gz
/opt/ornate-ems-gateway/venv/bin/python /root/kalpa-meter-hmi-v0.1/install.py
```

The installer checks package hashes and the three current HMI source hashes before
changing anything. It backs up the current HMI under /root/kalpa-meter-hmi-backup-*
and preserves file permissions. Separate HMI modifications cause a safe abort for
reconciliation. Already-installed identical files are accepted without reinstallation.
Use --check-only to preview compatibility. Application polling continues throughout.

Reload https://gw3-api.unityess.cloud/hmi/ with Ctrl+F5, sign in as usual, and select
Energy meter. While the physical meter is disconnected, Offline and dashes are
expected. Reconnecting should populate readings after the backend's next successful
poll. A backend startup or UI rendering check is not measurement commissioning.

If Cloudflare returns HTTP 530, that is a separate remote-access problem. Use an
SSH port forward when SSH is available:

```powershell
ssh -L 18000:127.0.0.1:8000 gw3-ssh.unityess
```

Keep the session open and visit http://localhost:18000/hmi/. With local Ethernet,
the same command can use root@192.168.10.2 instead of the Cloudflare alias.

## Rollback

Use the actual backup directory printed by the installer:

```bash
/opt/ornate-ems-gateway/venv/bin/python /root/kalpa-meter-hmi-v0.1/install.py --rollback /root/kalpa-meter-hmi-backup-TIMESTAMP
```

Refresh the browser afterward. Rollback refuses to overwrite changes made after
this update. It does not remove the separately installed backend meter driver or
stored history.

## Files and validation

Source: hmi/index.html, hmi/app.js, hmi/styles.css. Installer: install.py.
BASELINE.json records the supported previous HMI hashes. SHA256.json verifies this
package. See VALIDATION.txt for executed checks and any remaining limitations.

Developer checks (not required on the gateway): use pytest for test_installer.py;
install jsdom 26 in an isolated development directory and run tests/ui_dom_test.cjs
with Node.js. Fixtures and the original HMI baseline are included for reproducibility.
