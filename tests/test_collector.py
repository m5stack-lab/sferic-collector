import pytest

from sferic_collector.collector import Collector, CollectorError
from sferic_collector.models import Capability
from sferic_collector.sim import SimulatedStation
from sferic_collector.store import Store
from sferic_collector.transport import Fleet, StationToolError, StationTransport


@pytest.fixture
def collector(tmp_path):
    fleet = Fleet()
    fleet.add(SimulatedStation("sim-1"))
    return Collector(store=Store(tmp_path / "t.db"), fleet=fleet)


class _ToolErrStation(StationTransport):
    """Declares wifi but rejects the call with a typed device-side code."""

    id = "err-1"

    async def capability(self) -> Capability:
        return Capability(toolsets=["wifi"], radios=["wifi-2g4"])

    async def run(self, tool: str, params: dict) -> dict:
        raise StationToolError("device_only", "no headless path")


async def test_typed_device_error_passes_through(tmp_path):
    fleet = Fleet()
    fleet.add(_ToolErrStation())
    c = Collector(store=Store(tmp_path / "t.db"), fleet=fleet)
    with pytest.raises(CollectorError) as e:
        await c.dispatch("wifi.scan", "err-1")
    assert e.value.code == "device_only"


async def test_dispatch_supported_tool_stores_strike(collector):
    strike = await collector.dispatch("sensors.motion", "sim-1")
    assert strike.toolset == "sensors"
    assert strike.kind == "motion"
    assert "decoded" in strike.payload
    assert collector.store.strikes(station="sim-1")[0].id == strike.id


async def test_unknown_station(collector):
    with pytest.raises(CollectorError) as e:
        await collector.dispatch("sensors.motion", "nope")
    assert e.value.code == "station_not_found"


async def test_capability_not_present(collector):
    # the sim declares only ["sensors"]
    with pytest.raises(CollectorError) as e:
        await collector.dispatch("wifi.scan", "sim-1")
    assert e.value.code == "capability_not_present"


async def test_capability_disabled(collector):
    collector.disabled.add(("sim-1", "sensors.motion"))
    with pytest.raises(CollectorError) as e:
        await collector.dispatch("sensors.motion", "sim-1")
    assert e.value.code == "capability_disabled"


async def test_station_error_maps_through(collector):
    # sensors is declared, but the sim has no headless "sensors.mic"
    with pytest.raises(CollectorError) as e:
        await collector.dispatch("sensors.mic", "sim-1")
    assert e.value.code == "station_error"
