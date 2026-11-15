Kinetics Gateway V2.4.4 - Guarded BMS Insulation Sampling Stage
================================================================

Purpose
-------
Adds a direct staged CLI/API command for Rack BCU insulation sampling:
  Absolute Modbus register 0x0401 = 1
  Catalog key: start_insulation_sampleing

This is different from precharge:
  0x0401 = Start insulation sampling
  0x0402 = Start precharge

Safety behavior
---------------
The new command runs only when:
- Rack enable feedback is active.
- Rack voltage is inside the configured 1100-1500 V commissioning window.
- PCS is stopped with setpoint and actual power at zero.
- Positive and negative contactors are open.
- Precharge command and state are idle.
- E-stop is inactive.
- No documented detailed critical BMS fault is active.

The command writes value 1 only. It does not write 0 afterward because the
supplied vendor sheet does not specify an explicit clear. It waits at least
3 seconds before reading IR, IR+ and IR- values and documented fault status.

Important
---------
The supplied protocol does not define the project-specific acceptable IR
threshold or a definitive insulation-sampling-success state. Therefore this
patch reports values but does not automatically permit precharge. Review the
measurements with the BMS vendor before issuing 0x0402.

Install
-------
  cd /root/kinetics_bms_insulation_stage_patch_20260727
  sh deployment/update_bms_insulation_stage_on_imx93.sh

Run
---
  cd /opt/kinetics-gateway/backend
  /opt/kinetics-gateway/venv/bin/python tools/control_sequence_cli.py start-insulation \
    --pair-id pair_1 \
    --confirmation EXECUTE_STAGE_WRITE

Read again without writing:
  /opt/kinetics-gateway/venv/bin/python tools/control_sequence_cli.py verify-insulation \
    --pair-id pair_1

Do not run start-precharge until the insulation output has been reviewed.
