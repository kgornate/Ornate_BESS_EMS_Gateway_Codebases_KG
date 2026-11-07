# V3.1.0 — Hopewind Solar and ABB PCC Meter

- Adds `solar_plant` to the Flutter plant model and WebSocket delta merge path.
- Adds a dedicated Solar / PCC navigation page.
- Shows Hopewind generation, inverter state, yield and regulation telemetry.
- Shows ABB grid import/export, electrical measurements and energy counters.
- Adds overview cards and a complete searchable engineering-signal table.
- Keeps Hopewind diagnostic PCC fields separate from authoritative ABB values.
- Makes `solar_plant` selectable through the existing historian screen.
- Adds model and presentation tests for compact REST and live delta updates.

The gateway backend already polls, streams, diagnoses and stores the
`solar_plant` asset in the compressed SQLite historian. Solar dispatch remains
read-only/gated until its separate field-validation flag is enabled.
