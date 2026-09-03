"""End to end over a real socket: a uvicorn-hosted collector listener + a
real `websockets` client (`fake_station.run_station`) playing the firmware
role. Catches what the in-process TestClient can't — real TCP, the
concurrent reader task, disconnect/cleanup timing.
"""

import asyncio
import contextlib

import pytest
import uvicorn

from sferic_collector.collector import Collector
from sferic_collector.fake_station import run_station
from sferic_collector.store import Store
from sferic_collector.transport import Fleet
from sferic_collector.web import make_app


async def _serve(app) -> tuple[uvicorn.Server, int, asyncio.Task]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started and server.servers:
            break
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, port, task


async def _wait_for(pred, timeout=5.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.fixture
async def live(tmp_path):
    c = Collector(store=Store(tmp_path / "t.db"), fleet=Fleet())
    server, port, serve_task = await _serve(make_app(c, tokens={"tok"}))
    url = f"ws://127.0.0.1:{port}/station"
    yield c, url
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(serve_task, timeout=5.0)


async def test_fake_station_connects_and_is_dispatchable(live):
    c, url = live
    station = asyncio.create_task(
        run_station(url, token="tok", station_id="stick-e2e", toolsets=["wifi", "sensors"])
    )
    try:
        assert await _wait_for(lambda: c.fleet.get("stick-e2e") is not None)
        st = c.fleet.get("stick-e2e")
        assert st.kind == "ws"
        assert (await st.capability()).toolsets == ["wifi", "sensors"]

        # one-shot, auto-routed (only station with "sensors")
        strike = await c.dispatch("sensors.motion")
        assert strike.station == "stick-e2e"
        assert "decoded" in strike.payload

        # bounded stream over the real socket
        flash, strikes = await c.stream_dispatch("wifi.monitor", "stick-e2e", {}, 1.0)
        assert len(strikes) >= 2
        assert all(s.flash == flash for s in strikes)
        assert c.store.flashes(station="stick-e2e")[0].strike_count == len(strikes)
    finally:
        station.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await station

    assert await _wait_for(lambda: c.fleet.get("stick-e2e") is None)


async def test_bad_token_never_joins(live):
    c, url = live
    station = asyncio.create_task(
        run_station(url, token="wrong", station_id="stick-bad", toolsets=["wifi"])
    )
    try:
        assert not await _wait_for(lambda: c.fleet.get("stick-bad") is not None, timeout=1.0)
    finally:
        station.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await station
