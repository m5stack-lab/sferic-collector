"""MCP surface (ADR-0003 §5). Fleet tools (`sferic.*`) plus one
`<toolset>.<verb>` tool per known capability; every hardware tool takes a
`station` argument. Tools are registered statically and gated at call time
against the target station's declared capability.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from .collector import Collector, CollectorError
from .models import RunResult, StationInfo
from .sim import SimulatedStation

# The `<toolset>.<verb>` tools this build exposes. A call still fails with
# capability_not_present if the target station does not declare the toolset.
STATION_TOOLS = [
    "sensors.motion",
    "sensors.gesture",
    "wifi.scan",
    "wifi.probe",
    "ble.scan",
    "ir.decode",
    "ir.capture",  # one-shot learn (non-emitting)
]

# Streaming tools: bounded-capture MCP calls that start a session, collect
# strikes for duration_ms, stop, and store them under one flash.
STREAM_TOOLS = [
    "wifi.monitor",
    "wifi.deauth",
    "wifi.handshake",
    "wifi.wardrive",
    "wifi.portal",
    "ble.spam",
    "sensors.mic",
]

# Emitting tools: two-step arm/fire (ADR-0008 §1). Call without confirm_token
# to arm; call again with the returned token to fire.
EMIT_TOOLS = [
    "ir.tvbgone",
    "ir.replay",
    "ble.hid",
    "usb.hid",
]


def build_server(collector: Collector | None = None) -> MCPServer:
    c = collector or Collector()
    if not c.fleet.all():
        # SFERIC_SERIAL_PORT=/dev/tty... attaches a real tethered stick;
        # otherwise fall back to the in-process simulator.
        port = os.environ.get("SFERIC_SERIAL_PORT")
        if port:
            from .serial_station import SerialStation

            c.fleet.add(SerialStation(port))
        else:
            c.fleet.add(SimulatedStation())

    srv = MCPServer("sferic-collector")

    @srv.tool(name="sferic.stations")
    async def sferic_stations() -> list[dict]:
        """List every station the collector knows, with its declared capability."""
        out: list[dict] = []
        for st in c.fleet.all():
            cap = await st.capability()
            out.append(
                StationInfo(
                    id=st.id, model=st.model, fw_version=st.fw_version, capability=cap
                ).model_dump()
            )
        return out

    @srv.tool(name="sferic.strikes")
    def sferic_strikes(
        station: str | None = None, flash: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Query stored strikes, newest first."""
        return [s.model_dump() for s in c.store.strikes(station, flash, limit)]

    @srv.tool(name="sferic.flashes")
    def sferic_flashes(station: str | None = None) -> list[dict]:
        """List capture sessions (flashes)."""
        return [f.model_dump() for f in c.store.flashes(station)]

    for tool_name in STATION_TOOLS:
        _register_station_tool(srv, c, tool_name)
    for tool_name in STREAM_TOOLS:
        _register_stream_tool(srv, c, tool_name)
    for tool_name in EMIT_TOOLS:
        _register_emit_tool(srv, c, tool_name)

    return srv


def _register_station_tool(srv: MCPServer, c: Collector, tool_name: str) -> None:
    async def _fn(station: str, params: dict[str, Any] | None = None) -> RunResult:
        try:
            strike = await c.dispatch(tool_name, station, params)
        except CollectorError as e:
            return RunResult(ok=False, error=e.code, message=e.message)
        return RunResult(ok=True, strike=strike.model_dump())

    _fn.__name__ = tool_name.replace(".", "_")
    _fn.__doc__ = (
        f"Run {tool_name} on <station> headlessly. On success the result is "
        f"stored as a strike and returned. On failure returns a typed error "
        f"(capability_not_present | capability_disabled | station_not_found | "
        f"station_error)."
    )
    srv.tool(name=tool_name)(_fn)


def _register_stream_tool(srv: MCPServer, c: Collector, tool_name: str) -> None:
    async def _fn(
        station: str, duration_ms: int = 10000, params: dict[str, Any] | None = None
    ) -> dict:
        dur = max(1000, min(int(duration_ms), 120000)) / 1000.0
        try:
            flash, strikes = await c.stream_dispatch(tool_name, station, params, dur)
        except CollectorError as e:
            return {"ok": False, "error": e.code, "message": e.message}
        return {
            "ok": True,
            "flash": flash,
            "count": len(strikes),
            "strikes": [s.model_dump() for s in strikes],
        }

    _fn.__name__ = tool_name.replace(".", "_") + "_stream"
    _fn.__doc__ = (
        f"Run {tool_name} as a bounded streaming capture on <station> for "
        f"duration_ms (1000–120000). Strikes are stored under one flash and "
        f"returned. Typed errors as for the one-shot tools."
    )
    srv.tool(name=tool_name)(_fn)


def _register_emit_tool(srv: MCPServer, c: Collector, tool_name: str) -> None:
    async def _fn(
        station: str,
        params: dict[str, Any] | None = None,
        confirm_token: str | None = None,
    ) -> dict:
        try:
            r = await c.emit_dispatch(tool_name, station, params, confirm_token)
        except CollectorError as e:
            return {"ok": False, "error": e.code, "message": e.message}
        return {"ok": True, **r}

    _fn.__name__ = tool_name.replace(".", "_") + "_emit"
    _fn.__doc__ = (
        f"Emitting tool {tool_name} — TWO STEPS. Call once WITHOUT "
        f"confirm_token to arm: nothing is emitted, you get back "
        f"{{confirm_token, expires_ms}}. Call again WITH that token to fire. "
        f"params: ble.hid / usb.hid -> {{'text': '...'}}; ir.replay -> "
        f"{{'raw': [µs,…]}} or {{'proto': N, 'value': '0x…', 'bits': N}}; "
        f"ir.tvbgone -> none."
    )
    srv.tool(name=tool_name)(_fn)
