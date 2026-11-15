# Gateway safe-stop/runtime-monitor race hotfix

This patch sits on top of the verified Stage 3C control service.

It corrects the shutdown race observed when an operator safe-stop opened the selected BMS contactors while the active runtime monitor still evaluated the previous non-zero command.

Changes:

- signals the pair runtime monitor to stop before physical safe-stop writes;
- sets runtime commanded/requested power to zero before BMS opening;
- rechecks the stop signal after a cache/live read returns;
- replaces monitor `sleep()` with an interruptible event wait;
- serializes physical safe-stop execution per pair;
- applies the same ordering to operator abort;
- exposes `safe_stop_monitor_quiesce: true` in runtime-monitor diagnostics.

No PCS/BMS register addresses, power sign convention, automatic sequence order, ramp logic, limits or fault checks are changed.

Install only while all four pairs are at the verified safe baseline:

```sh
cd /root/kinetics_gateway_stage3d_safestop_hotfix_20260728
sh ./install_on_gateway.sh
sh ./verify_patch.sh
```
