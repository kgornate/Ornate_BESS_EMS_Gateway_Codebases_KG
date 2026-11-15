Kinetics Gateway V2.4.1 - guarded BMS one-click recovery patch

Purpose:
- Add a direct staged CLI command: recover-bms
- Pulse BAU register 0x3047 (one-click recovery) as 1 then 0
- Permit recovery only from a verified de-energized state
- Read fault snapshots before and after the recovery request
- Preserve all active configuration, BMS/PCS protocol catalogs, networking,
  Cloudflare, systemd, historian, database and logs

This patch does not enable automatic sequencing and does not start a rack,
close contactors, start PCS or issue a power command.

Install:
  cd <extracted-folder>
  sh deployment/update_bms_recovery_on_imx93.sh

Run recovery only after the installer succeeds:
  cd /opt/kinetics-gateway/backend
  /opt/kinetics-gateway/venv/bin/python tools/control_sequence_cli.py recover-bms \
    --pair-id pair_1 --confirmation EXECUTE_STAGE_WRITE
