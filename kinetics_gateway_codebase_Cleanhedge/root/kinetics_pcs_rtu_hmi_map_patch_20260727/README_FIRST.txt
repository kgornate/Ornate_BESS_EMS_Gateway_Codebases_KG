Kinetics Gateway PCS RTU HMI-map patch v2.3

This patch replaces only PCS-specific application files/catalogs. It preserves:
- /etc/kinetics-gateway/config.json
- BMS driver and BMS catalog
- networking and network guard
- systemd units
- Cloudflare, authentication, historian and storage

Install:
  cd /root/kinetics_pcs_rtu_hmi_map_patch_20260727
  sh deployment/update_pcs_hmi_map_on_imx93.sh

Then, after confirming /dev/ttyUSB0 and disconnecting the Windows HMI:
  sh /opt/kinetics-gateway/backend/deployment/enable_pcs1_readonly_on_imx93.sh

Writes stay disabled. PCS2-PCS4 stay disabled pending actual slave IDs.
