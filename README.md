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

## CVE enrichment

`wifi.scan` and `ble.scan` results are automatically enriched on ingest
with a best-effort `security` block per AP/device: vendor (via a live
MAC-OUI lookup) and, when the vendor is known to
[cve-search](https://cve.circl.lu), a CVE count/list if the scanned
device's own SSID/name matches one of that vendor's known products. This
is a **lead**, not a confirmed finding — no firmware/version fingerprinting
exists yet. See [ADR-0010](https://github.com/ry-ops/sferic-sticks3/blob/main/docs/adr/0010-cve-enrichment.md).

## MCP surface

| Tool | Purpose |
|---|---|
| `sferic.stations` | list stations + declared `{toolsets, radios}` |
| `sferic.strikes` | query stored strikes (`station?`, `flash?`, `limit`) |
| `sferic.flashes` | list capture sessions |
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
  serial_station.py  SerialStation — USB '>'/'<' protocol + '!' strike stream
  collector.py    dispatch + stream_dispatch + emit_dispatch: capability
                  gating, strike/flash store, two-step emit arm/fire
  oui.py          MAC OUI -> vendor (macvendors.com, ADR-0010)
  cve.py          vendor/product -> CVEs (cve-search, ADR-0010)
  enrichment.py   wires oui.py + cve.py into wifi.scan / ble.scan ingest
  server.py       MCPServer surface (one-shot + bounded-stream tools)
tests/
```

## License

MIT © 2026 ry-ops
