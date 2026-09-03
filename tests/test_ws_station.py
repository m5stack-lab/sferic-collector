"""WebSocketStation framing tests with a fake websocket — no network.

The fake calls a `responder(cmd)` for each outbound `cmd` frame and feeds
the frames it yields back on the inbound queue (log-free, unlike serial).
"""

import asyncio
import json

import pytest

from sferic_collector.collector import Collector
from sferic_collector.store import Store
from sferic_collector.transport import Fleet, StationError, StationToolError
from sferic_collector.ws_station import WebSocketStation

_CLOSE = object()


class FakeWS:
    def __init__(self, responder):
        self._responder = responder
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def send_text(self, s: str) -> None:
        msg = json.loads(s)
        self.sent.append(msg)
        if msg.get("type") == "cmd":
            for frame in self._responder(msg):
                await self._inbox.put(frame)

    async def receive_text(self) -> str:
        frame = await self._inbox.get()
        if frame is _CLOSE:
            raise RuntimeError("websocket closed")
        return json.dumps(frame)

    async def close(self) -> None:
        self.closed = True
        await self._inbox.put(_CLOSE)

    # test helper: push an unsolicited inbound frame
    async def feed(self, frame: dict) -> None:
        await self._inbox.put(frame)


def _station(responder, **info) -> tuple[WebSocketStation, FakeWS]:
    ws = FakeWS(responder)
    st = WebSocketStation(
        ws,
        {
            "id": "stick-ws1",
            "model": "m5stack-sticks3",
            "toolsets": ["wifi", "sensors"],
            "radios": ["wifi-2g4"],
            **info,
        },
        rpc_timeout=1.0,
    )
    return st, ws


async def test_identity_and_capability_from_hello():
    st, _ = _station(lambda c: [])
    assert st.id == "stick-ws1"
    assert st.kind == "ws"
    cap = await st.capability()
    assert cap.toolsets == ["wifi", "sensors"]
    assert "wifi-2g4" in cap.radios


async def test_run_ok_returns_result():
    def responder(cmd):
        assert cmd["op"] == "run"
        yield {"type": "reply", "id": cmd["id"], "ok": True, "result": {"moving": False}}

    st, _ = _station(responder)
    await st.start()
    assert await st.run("sensors.motion", {}) == {"moving": False}
    await st.close()


async def test_run_typed_error_raises():
    def responder(cmd):
        yield {"type": "reply", "id": cmd["id"], "ok": False, "error": "device_only"}

    st, _ = _station(responder)
    await st.start()
    with pytest.raises(StationToolError) as e:
        await st.run("wifi.scan", {})
    assert e.value.code == "device_only"
    await st.close()


async def test_stream_collects_only_strike_frames():
    def responder(cmd):
        if cmd["op"] == "stream_start":
            yield {"type": "reply", "id": cmd["id"], "ok": True, "flash": "f00d"}
            yield {"type": "event", "flash": "f00d", "event": "open"}
            for i in range(3):
                yield {
                    "type": "strike",
                    "flash": "f00d",
                    "id": f"f00d-{i}",
                    "toolset": "wifi",
                    "kind": "monitor",
                    "ts": f"t+{i}",
                    "payload": {"decoded": {"rate": i}},
                }
        elif cmd["op"] == "stream_stop":
            yield {"type": "event", "flash": "f00d", "event": "close", "count": 3}
            yield {"type": "reply", "id": cmd["id"], "ok": True, "count": 3}

    st, _ = _station(responder)
    await st.start()
    res = await st.stream("wifi.monitor", {}, duration_s=0.1)
    assert res["flash"] == "f00d"
    assert len(res["strikes"]) == 3
    assert res["count"] == 3
    assert all(s["type"] == "strike" for s in res["strikes"])
    await st.close()


async def test_ping_is_answered():
    st, ws = _station(lambda c: [])
    await st.start()
    await ws.feed({"type": "ping"})
    await asyncio.sleep(0.05)
    assert {"type": "pong"} in ws.sent
    await st.close()


async def test_cmd_after_link_close_raises_stationerror():
    st, _ = _station(lambda c: [])
    await st.start()
    await st.close()
    with pytest.raises(StationError):
        await st.run("sensors.motion", {})


async def test_reader_disconnect_fails_pending():
    # responder returns nothing, so the rpc future never resolves; then the
    # link drops and the pending call must fail fast (not hang to timeout).
    st, ws = _station(lambda c: [])
    await st.start()
    task = asyncio.create_task(st.run("sensors.motion", {}))
    await asyncio.sleep(0.02)
    await ws.close()
    with pytest.raises(StationError):
        await task


async def test_stream_dispatch_through_collector_persists(tmp_path):
    def responder(cmd):
        if cmd["op"] == "stream_start":
            yield {"type": "reply", "id": cmd["id"], "ok": True, "flash": "beef"}
            for i in range(2):
                yield {
                    "type": "strike",
                    "flash": "beef",
                    "id": f"beef-{i}",
                    "toolset": "wifi",
                    "kind": "monitor",
                    "ts": f"t+{100 + i}",
                    "payload": {"decoded": {"rate": i}},
                }
        elif cmd["op"] == "stream_stop":
            yield {"type": "reply", "id": cmd["id"], "ok": True, "count": 2}

    st, _ = _station(responder)
    await st.start()
    fleet = Fleet()
    fleet.add(st)
    c = Collector(store=Store(tmp_path / "t.db"), fleet=fleet)

    flash_id, strikes = await c.stream_dispatch("wifi.monitor", "stick-ws1", {}, 0.1)
    assert flash_id == "beef"
    assert len(strikes) == 2
    assert all(s.flash == "beef" for s in strikes)
    assert strikes[0].meta["device_ts"] == "t+100"
    assert c.store.flashes(station="stick-ws1")[0].strike_count == 2
    await st.close()
