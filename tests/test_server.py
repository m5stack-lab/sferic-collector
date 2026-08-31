import json

import pytest

from sferic_collector.collector import Collector
from sferic_collector.server import build_server
from sferic_collector.sim import SimulatedStation
from sferic_collector.store import Store
from sferic_collector.transport import Fleet


@pytest.fixture
def srv(tmp_path):
    fleet = Fleet()
    fleet.add(SimulatedStation("sim-1"))
    return build_server(Collector(store=Store(tmp_path / "t.db"), fleet=fleet))


async def test_tools_registered(srv):
    names = {t.name for t in await srv.list_tools()}
    assert {"sferic.stations", "sferic.strikes", "sferic.flashes"} <= names
    assert {"sensors.motion", "wifi.scan"} <= names


async def test_stations_tool(srv):
    payload = _payload(await srv.call_tool("sferic.stations", {}))
    assert payload[0]["id"] == "sim-1"
    assert payload[0]["capability"]["toolsets"] == ["sensors"]


async def test_run_tool_ok_and_persisted(srv):
    out = _payload(await srv.call_tool("sensors.motion", {"station": "sim-1"}))
    assert out["ok"] is True
    assert out["strike"]["toolset"] == "sensors"

    strikes = _payload(await srv.call_tool("sferic.strikes", {"station": "sim-1"}))
    assert len(strikes) == 1
    assert strikes[0]["id"] == out["strike"]["id"]


async def test_run_tool_capability_error(srv):
    out = _payload(await srv.call_tool("wifi.scan", {"station": "sim-1"}))
    assert out["ok"] is False
    assert out["error"] == "capability_not_present"


def _payload(result):
    """Pull the structured payload out of a CallToolResult, falling back to
    the JSON text block."""
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        # tools returning a bare list get wrapped as {"result": [...]}
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)
