# Kinetics Gateway Phase 2 — Priority I/O Arbiter

This patch adds transaction-boundary priority arbitration without changing any
Modbus register, function code, scaling, word order, control value, control
sequence, SOC threshold, pair order, or API contract.

Priority order:

1. safety / safe-stop
2. control writes
3. control readback and verification
4. routine telemetry
5. historian/engineering background work (reserved lane)

The four PCS devices share one prioritized RTU lane. BMS TCP ports each use an
independent prioritized lane, so unrelated physical endpoints do not block each
other. Aging prevents indefinite starvation. Queue, wait and timeout counters
are exposed under `io_arbiter` in `/api/health`.

Installation does not start the scheduler and does not intentionally issue any
charge or discharge command. If the scheduler was already active, its prior
state is restored only after the gateway and tests pass.

Install:

    cd /root/kinetics_phase2_io_arbiter_20261103
    CONFIRM_IDLE=YES sh install_phase2_io_arbiter.sh

Read-only verification:

    sh verify_phase2_io_arbiter.sh
