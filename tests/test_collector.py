import pytest

from sferic_collector.collector import Collector, CollectorError
from sferic_collector.sim import SimulatedStation
from sferic_collector.store import Store
from sferic_collector.transport import Fleet


@pytest.fixture
def collector(tmp_path):
    fleet = Fleet()
    fleet.add(SimulatedStation("sim-1"))
    return Collector(store=Store(tmp_path / "t.db"), fleet=fleet)


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
