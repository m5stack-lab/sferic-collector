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
