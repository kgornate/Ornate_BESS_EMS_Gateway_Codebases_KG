# Kinetics Gateway Phase 3 — Reliable Command Execution

This patch preserves every existing register, function code, encoded command
value, scaling rule, sequence, safety threshold and pair order.

It changes command-outcome handling only:

1. FC06/FC16 writes are never automatically replayed after transport loss.
2. Successful writes require authoritative register readback.
3. A lost response followed by matching readback is recorded as reconciled.
4. A lost response without conclusive readback remains explicitly ambiguous.
5. Persistent-register readback mismatch is never reported as success.
6. Edge-trigger commands retain state-based verification because they may
   legitimately self-clear before immediate register readback.

Installation and verification tests use fake transports and mocked control
devices. They do not issue field charging or discharging commands.

Install:

    cd /root/kinetics_phase3_reliable_commands_20261103
    CONFIRM_IDLE=YES sh install_phase3_reliable_commands.sh

Read-only verification:

    sh verify_phase3_reliable_commands.sh
