Kinetics Gateway V2.4.3 - documented BMS fault-gate commissioning patch

Purpose:
- Keep the raw 0x1001 summary system-fault bit visible for diagnostics.
- Use documented detailed critical-fault registers as the actual energising-command blocker:
  0x1004, 0x1007, 0x100C, 0x100D, 0x0011, 0x0012, 0x0015.
- Ignore Level-3 warnings such as Access Door Feedback for this staged commissioning path.
- Do NOT bypass emergency stop, PCS fault shutdown, BMS current limits, or charge/discharge prohibition checks.

Install:
  sh deployment/update_bms_documented_fault_gate_on_imx93.sh

The installer sends no hardware command.
