Kinetics Gateway V2.4.2 - BMS Recovery Trigger Hotfix

Reason:
The BMS protocol defines BAU register 0x3047 as:
  0 = invalid
  1 = trigger one-click recovery

The previous recovery implementation wrote 1 and then explicitly wrote 0.
The BMS accepted the trigger but rejected the explicit clear with Modbus
exception 7 for FC06. This hotfix writes only value 1 and then monitors the
BMS fault feedback.

Install on FRDM:
  cd /root/kinetics_bms_recovery_trigger_hotfix_20260727
  sh deployment/update_bms_recovery_trigger_on_imx93.sh

The installer performs no hardware command.
