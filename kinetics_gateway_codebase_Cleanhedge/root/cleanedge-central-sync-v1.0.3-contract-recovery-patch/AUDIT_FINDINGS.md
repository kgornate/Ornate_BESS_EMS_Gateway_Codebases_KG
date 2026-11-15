# Focused audit findings

- The frozen CleanEdge workbook assigns the ABB MFM to S2.10
  `combiner_box_meter` and HopeComBox/string-inverter telemetry to S2.11
  `solar_inverter`.
- Deployed classification used signal-name heuristics that moved some
  solar-origin `grid_*` points into S2.10. Asset identity must win over the
  signal prefix.
- Field status showed `substream_excluded` for S2.10, returned as retryable.
  Those messages repeatedly occupied the live queue.
- Live-health protection then correctly limited/paused archive recovery. The
  scheduler itself was not stuck; it was responding to a poisoned live queue.
- This patch fixes classification and terminal-rejection handling. It does not
  bypass live priority or weaken memory/load/live-age safety gates.
- Backend enablement of S2.10 is still required for ABB MFM acceptance.
