# Phase 3B — Read-only solar/PCC telemetry

Adds the HopeComBox data logger at 172.16.0.111:502, Unit ID 1 as `solar_plant`.
It polls FC03 registers 2100-2125 and 2600-2622 through its own priority arbiter,
using low-word-first decoding confirmed from Nagpur field samples.

This patch exposes solar, PCC import/export, site load, meter status and aggregate
inverter telemetry through the existing asset, live-stream and historian paths.
It intentionally exposes no solar write API. Solar dispatch remains gated until a
daytime field test freezes the import/export direction convention.

Install only with the scheduler inactive:

    CONFIRM_IDLE=YES bash install_phase3b_solar_telemetry.sh
