# BMS fault-recovery commissioning stage

## Current confirmed condition

- PCS 1 Modbus RTU reads and writes are working.
- BMS Modbus TCP reads and writes are working.
- Rack 1 enable command and readback worked.
- Precharge command 0x0402 accepted and read back, but the physical precharge
  sequence did not start; contactors remained open and PCS DC bus stayed near 0 V.
- BMS summary register 0x1001 later reported 0x1C00: system fault, charge
  prohibited and discharge prohibited.
- Documented BAU/Rack Level-1 fault registers were zero. Rack 1 register 0x0011
  reported 0x4000 on a protocol-reserved bit, so the supplied protocol does not
  identify the underlying vendor-specific/latched cause.

## Recovery command behavior

The recover-bms command verifies all of the following before writing:

- PCS state is stopped.
- PCS active-power setpoint is zero.
- PCS actual active power is near zero.
- Rack 1 is disabled.
- Positive and negative contactors are open.
- Precharge command is zero.
- Emergency stop is not active.
- BMS system-fault summary is active.

It then pulses BAU 0x3047 from 0 -> 1 -> 0, samples the BMS status, and reports
whether the summary system fault cleared. It does not bypass a fault. If the
fault remains active, do not enable the rack; use the BMS HMI/vendor definition
for the reserved fault bit.
