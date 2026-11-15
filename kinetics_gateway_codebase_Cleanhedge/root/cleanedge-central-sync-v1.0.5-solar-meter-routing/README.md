# CleanEdge Central Sync v1.0.5 — Solar/Grid-Meter Routing

Moves the 12 CleanEdge solar/grid-meter policies from the backend-excluded
`combiner_box_meter` substream to the agreed S2.11 `solar_inverter` substream.

This is a routing-only correction. It preserves all point IDs, values, units,
quality, cadence, retention, runtime multiplicity, message fields and polling.
It does not enable cloud commands or modify automation/control behavior.

Existing historical `combiner_box_meter` messages remain in the durable
outbox/archives and are terminally resolved by the installed v1.0.3 logic.

Install:

```sh
sha256sum -c SHA256SUMS
./deployment/install.sh
```

Rollback:

```sh
./deployment/rollback.sh
```
