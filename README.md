# sferic-collector

The **sferic** fleet collector — the companion MCP server from
[ADR-0003](https://github.com/ry-ops/sferic-sticks3/blob/main/docs/adr/0003-companion-mcp-server.md).
Host-side control plane: stations connect to it, MCP clients (agents)
connect to it, it holds fleet state + the strike/flash store and dispatches
`<toolset>.<verb>` calls to a target station's headless interface.

Firmware lives in [`ry-ops/sferic-sticks3`](https://github.com/ry-ops/sferic-sticks3); the
per-toolset wire contracts are its
[`docs/mcp/`](https://github.com/ry-ops/sferic-sticks3/tree/main/docs/mcp).

## Status

**Milestone 2 — real stick over USB serial.** `SerialStation` speaks the
firmware's `>`/`<` line protocol
([`docs/mcp/serial-transport.md`](https://github.com/ry-ops/sferic-sticks3/blob/main/docs/mcp/serial-transport.md)):
`hello` → capability, `run` → `Registry::run` dispatch, typed errors passed
straight through. Verified end to end — the collector runs `sensors.motion`
on a tethered StickS3 and stores the strike.

Milestone 1 (skeleton, capability gating, SQLite store, in-process
**simulated station**) is the fallback when no port is set.

Not built yet: WebSocket/MQTT transport (Pi phase, ADR-0009), Redis (hot
fleet state / job queue), Qdrant (semantic recall), the dashboard, streaming
flashes, emitter confirm-flows. See ADR-0007 §3 / ADR-0008.

## Run

```sh
uv sync
uv run sferic-collector                              # sim station, stdio MCP
SFERIC_SERIAL_PORT=/dev/cu.usbmodem1101 uv run sferic-collector   # tethered stick
uv run pytest
uv run ruff check
```

Register with Claude Code:

```sh
claude mcp add sferic-collector -- uv --directory /path/to/sferic-collector run sferic-collector
# add --env SFERIC_SERIAL_PORT=/dev/cu.usbmodem1101 for a real stick
```

## MCP surface

| Tool | Purpose |
|---|---|
| `sferic.stations` | list stations + declared `{toolsets, radios}` |
| `sferic.strikes` | query stored strikes (`station?`, `flash?`, `limit`) |
| `sferic.flashes` | list capture sessions |
| `sensors.motion` / `sensors.gesture` | one-shot IMU read on `station` → stored as a strike |
| `wifi.scan` / `ble.scan` | registered; return `capability_not_present` until a station declares the toolset |

Every hardware tool takes a `station` argument. Failures come back typed
(ADR-0003 §3): `station_not_found`, `capability_not_present`,
`capability_disabled`, `device_only` (registered but no headless path yet),
`station_error`.

## Layout

```
src/sferic_collector/
  models.py       Strike / Flash / Capability  (strike-format.md v1)
  store.py        SQLite strike + flash store
  transport.py    StationTransport ABC + Fleet + typed errors
  sim.py          SimulatedStation — in-process fake
  serial_station.py  SerialStation — USB '>'/'<' line protocol
  collector.py    dispatch: capability gating + strike wrapping
  server.py       MCPServer surface
tests/
```

## License

MIT © 2026 ry-ops
