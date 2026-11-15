Kinetics Gateway V2.4 staged BMS/PCS control patch

Install on the already-working V2.3 FRDM gateway:

  cd <extracted-patch-folder>
  sh deployment/update_staged_control_on_imx93.sh

The updater preserves /etc/kinetics-gateway/config.json, BMS catalog, network files,
systemd units/drop-ins, Cloudflare configuration, database and logs.
It restarts the gateway in its existing mode and does not enable or execute writes.

After reviewing STAGED_BMS_PCS_CONTROL.md, arm only the manual stage APIs with:

  sh /opt/kinetics-gateway/backend/deployment/enable_staged_control_on_imx93.sh ENABLE_STAGE_WRITES

Full automatic sequencing remains disabled.
