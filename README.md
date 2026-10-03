# sferic-collector

The **sferic** fleet collector — the companion MCP server from
[ADR-0003](https://github.com/m5stack-lab/sferic-sticks3/blob/main/docs/adr/0003-companion-mcp-server.md).
Host-side control plane: stations connect to it, MCP clients (agents)
connect to it, it holds fleet state + the strike/flash store and dispatches
`<toolset>.<verb>` calls to a target station's headless interface.

Firmware lives in [`m5stack-lab/sferic-sticks3`](https://github.com/m5stack-lab/sferic-sticks3); the
per-toolset wire contracts are its
[`docs/mcp/`](https://github.com/m5stack-lab/sferic-sticks3/tree/main/docs/mcp).

## Status

**Milestone 3 — networked stations (WebSocket).** `WebSocketStation` +
`web.py`: a Starlette listener stations dial into
(`ws://<collector>:<port>/station`), same op vocabulary as serial, framed as
`{"type":…}` JSON text frames
([`docs/mcp/ws-transport.md`](https://github.com/m5stack-lab/sferic-sticks3/blob/main/docs/mcp/ws-transport.md),
ADR-0009). Per-station bearer token on the upgrade; a dropped socket removes
the station and kills its stream session. Runs in the same process as the
stdio MCP server when `SFERIC_WS_PORT` is set.

**Capability routing.** Every `<toolset>.<verb>` tool's `station` argument is
now optional — omit it and the collector routes to the one station that
declares the toolset (`no_capable_station` / `ambiguous_station` otherwise).

**Milestone 2 — real stick over USB serial.** `SerialStation` speaks the
firmware's `>`/`<` line protocol
([`docs/mcp/serial-transport.md`](https://github.com/m5stack-lab/sferic-sticks3/blob/main/docs/mcp/serial-transport.md)).
Milestone 1 (skeleton, capability gating, SQLite store, in-process
**simulated station**) is the fallback when nothing else is attached.

**Recall.** `sferic.recall` full-text-searches every stored strike (toolset,
kind, station, flash, and the flattened decoded payload), BM25-ranked, with
optional `station` / `toolset` / `kind` filters. Backed by SQLite FTS5 today
(zero extra deps); a semantic/embedding backend can replace `Store.search`
behind the same tool when the Pi lands.

**Dashboard.** With `SFERIC_WS_PORT` set the same process serves a
self-contained read-only page at `GET /` (stations, a recall search box,
recent strikes, flashes; polls every 4 s) plus JSON at `/api/stations`,
`/api/strikes`, `/api/flashes`, `/api/recall`. No build step, no external
assets — one HTML string in `web_dashboard.py`.

Not built yet: Redis (hot fleet state / job queue), the embedding backend
for `sferic.recall`, dashboard write-actions (it is read-only). See
ADR-0007 §3 / ADR-0008.

## Run

```sh
uv sync
uv run sferic-collector                              # sim station, stdio MCP
SFERIC_SERIAL_PORT=/dev/cu.usbmodem1101 uv run sferic-collector   # tethered stick
SFERIC_WS_PORT=8787 SFERIC_WS_TOKENS=tok1,tok2 uv run sferic-collector  # + station WS + dashboard on :8787
uv run pytest
uv run ruff check
```

WS env: `SFERIC_WS_PORT` (enables the listener), `SFERIC_WS_HOST` (default
`0.0.0.0`), `SFERIC_WS_TOKENS` (comma-separated accepted bearer tokens;
unset = accept any station, dev only). A station's config lives in the
firmware's gitignored `src/ws_config.h`.

**No hardware?** `sferic-fake-station` is a stand-in for the `sferic-ws`
firmware — connects over WebSocket, declares a capability set, and answers
`run` / `stream_*` / `emit_*` with synthetic data. Run one (or several) in
another terminal to exercise the collector, routing, and the dashboard:

```sh
uv run sferic-fake-station --token tok1 --id stick-fake --toolsets wifi,ble,ir,sensors
uv run sferic-fake-station --token tok1 --id tab5-fake  --toolsets sensors,ble
```

Register with Claude Code:

```sh
claude mcp add sferic-collector -- uv --directory /path/to/sferic-collector run sferic-collector
# add --env SFERIC_SERIAL_PORT=/dev/cu.usbmodem1101 for a real stick
```

## MCP surface

| Tool | Purpose |
|---|---|
| `sferic.stations` | list stations + declared `{toolsets, radios}`, `transport`, `online` |
| `sferic.strikes` | query stored strikes (`station?`, `flash?`, `limit`) |
| `sferic.flashes` | list capture sessions |
| `sferic.recall` | full-text search over strikes — `query` + `station?`/`toolset?`/`kind?` filters, ranked best-first (local FTS5; a semantic backend slots in behind this later) |
| `sensors.motion` / `sensors.gesture` | one-shot IMU read → strike |
| `wifi.scan` | AP scan → `{count, aps:[…]}` strike (`{"ms":…}` param) |
| `wifi.probe` | bounded probe-request capture → `{total, uniq, probes:[…]}` |
| `ble.scan` | BLE scan → `{count, devices:[…]}` |
| `ir.decode` | listen for one IR frame → `{protocol, value, bits}` |
| `ir.capture` | one-shot IR learn → `{protocol,value,bits,raw:[…]}` |
| `wifi.monitor` `wifi.deauth` `wifi.handshake` `wifi.wardrive` `wifi.portal` `ble.spam` `sensors.mic` | **bounded streaming** for `duration_ms` (1000–120000); strikes stored under one flash, returned as `{flash, count, strikes:[…]}` |
| `ir.tvbgone` `ir.replay` `ble.hid` `usb.hid` | **emit, two-step**: call without `confirm_token` to arm (returns `{confirm_token, expires_ms}`, nothing fires); call again with the token to fire. `ble.hid`/`usb.hid` take `params={"text":…}`; `ir.replay` takes `{"raw":[…]}` or `{"proto","value","bits"}` |

21 MCP tools cover every station capability (7 one-shot, 7 streaming, 4
emit, 3 fleet). Non-sensor tools return real data on a tethered stick
(radio free over serial); on the simulator they return
`capability_not_present`.

Every hardware tool takes an optional `station` argument (auto-routed by
toolset when omitted). Failures come back typed (ADR-0003 §3):
`station_not_found`, `no_capable_station`, `ambiguous_station`,
`capability_not_present`, `capability_disabled`, `device_only` (registered
but no headless path yet), `station_error`.

## Layout

```
src/sferic_collector/
  models.py       Strike / Flash / Capability + strike_text() (searchable projection)
  store.py        SQLite strike + flash store + FTS5 recall (Store.search)
  transport.py    StationTransport ABC + Fleet (id de-dup, capability routing)
  sim.py          SimulatedStation — in-process fake
  serial_station.py  SerialStation — USB '>'/'<' protocol + '!' strike stream
  ws_station.py   WebSocketStation — {"type":…} frames over one socket (ADR-0009)
  web.py          Starlette HTTP+WS surface — /station, dashboard /, /api/*
  web_dashboard.py  the dashboard page (one self-contained HTML string)
  fake_station.py   `sferic-fake-station` — a firmware stand-in over WebSocket
  collector.py    dispatch + stream + emit: capability gating/routing, store
  server.py       MCPServer surface (one-shot + stream + emit + sferic.recall)
tests/
```

## License

MIT © 2026 ry-ops
