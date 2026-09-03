"""Capability routing: `station` is optional — the collector picks the one
station that declares the toolset, or raises a typed error."""

import pytest

from sferic_collector.collector import Collector, CollectorError
from sferic_collector.sim import SimulatedStation
from sferic_collector.store import Store
from sferic_collector.transport import Fleet


def _collector(tmp_path, *stations) -> Collector:
    fleet = Fleet()
    for s in stations:
        fleet.add(s)
    return Collector(store=Store(tmp_path / "t.db"), fleet=fleet)


async def test_auto_route_single_capable_station(tmp_path):
    c = _collector(tmp_path, SimulatedStation("sim-1"))
    strike = await c.dispatch("sensors.motion")  # no station named
    assert strike.station == "sim-1"


async def test_auto_route_no_capable_station(tmp_path):
    c = _collector(tmp_path, SimulatedStation("sim-1"))  # declares only "sensors"
    with pytest.raises(CollectorError) as e:
        await c.dispatch("wifi.scan")
    assert e.value.code == "no_capable_station"


async def test_auto_route_ambiguous(tmp_path):
    c = _collector(tmp_path, SimulatedStation("sim-1"), SimulatedStation("sim-2"))
    with pytest.raises(CollectorError) as e:
        await c.dispatch("sensors.motion")
    assert e.value.code == "ambiguous_station"


async def test_explicit_station_still_resolves(tmp_path):
    c = _collector(tmp_path, SimulatedStation("sim-1"), SimulatedStation("sim-2"))
    strike = await c.dispatch("sensors.motion", "sim-2")
    assert strike.station == "sim-2"


async def test_explicit_unknown_station(tmp_path):
    c = _collector(tmp_path, SimulatedStation("sim-1"))
    with pytest.raises(CollectorError) as e:
        await c.dispatch("sensors.motion", "nope")
    assert e.value.code == "station_not_found"


async def test_fleet_add_replaces_same_id(tmp_path):
    fleet = Fleet()
    a, b = SimulatedStation("stick-6e27ac"), SimulatedStation("stick-6e27ac")
    fleet.add(a)
    fleet.add(b)
    assert fleet.all() == [b]  # the reconnect superseded the stale entry


async def test_fleet_keeps_distinct_serial_placeholders(tmp_path):
    from sferic_collector.serial_station import SerialStation

    fleet = Fleet()
    fleet.add(SerialStation(port="/dev/ttyA"))
    fleet.add(SerialStation(port="/dev/ttyB"))
    assert len(fleet.all()) == 2  # "serial:*" placeholder ids are not de-duped


async def test_fleet_remove(tmp_path):
    fleet = Fleet()
    fleet.add(SimulatedStation("sim-1"))
    fleet.remove("sim-1")
    assert fleet.all() == []
