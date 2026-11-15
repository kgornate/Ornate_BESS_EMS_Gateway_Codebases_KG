KINETICS GATEWAY V2.4.5 PATCH
PCS PRECHARGE-STANDBY PREPARATION

Purpose
-------
This patch adds a guarded commissioning command that prepares PCS1 in:

  Remote + PQ + Constant Power + Start/Standby + 0 kW

before enabling BMS Rack1 and requesting BMS precharge.

It does NOT:
- enable Rack1,
- write BMS precharge 0x0402,
- write BMS insulation command 0x0401,
- apply charge/discharge power,
- enable the full automatic state machine.

Installation on i.MX93
----------------------
1. Copy ZIP to /root and unzip it.
2. Enter the extracted directory.
3. Run:

   sh deployment/update_pcs_precharge_standby_on_imx93.sh

The installer backs up the files being replaced, compiles the Python code,
restarts kinetics-gateway.service, and performs no hardware write command.

New command
-----------

  cd /opt/kinetics-gateway/backend

  /opt/kinetics-gateway/venv/bin/python \
  tools/control_sequence_cli.py prepare-pcs-standby \
  --pair-id pair_1 \
  --confirmation EXECUTE_STAGE_WRITE

Proceed to Rack Enable and BMS Precharge ONLY when the output contains:

  "ok": true
  "ready_for_bms_precharge": true
  "pcs_operating_state": 8
  "pcs_remote_feedback": true
  "pcs_authorized": true
  "pcs_power_setpoint_kw": 0.0
  "pcs_actual_power_kw": approximately 0.0

If the command fails or times out, it automatically requests PCS 0 kW and Stop.
Still verify the physical PCS/BMS state and use safe-stop if anything is abnormal.

Emergency safe-stop
-------------------

  /opt/kinetics-gateway/venv/bin/python \
  tools/control_sequence_cli.py safe-stop \
  --pair-id pair_1 \
  --open-bms \
  --confirmation EXECUTE_STAGE_WRITE

Important
---------
- Hardware/vendor team must be present.
- Physical E-stop must be accessible.
- Personnel must remain clear of energized equipment.
- Only one Modbus master may control PCS/BMS.
- Do not use start-insulation; 0x0401 is now blocked and managed internally by BMS.
