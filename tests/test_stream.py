"""Streaming path: SerialStation._stream_blocking + Collector.stream_dispatch,
FakeSerial-backed, no hardware."""

import json

import pytest

from sferic_collector.collector import Collector, CollectorError
from sferic_collector.serial_station import SerialStation
from sferic_collector.store import Store
from sferic_collector.transport import Fleet


class FakeSerial:
    is_open = True
    dtr = False

    def __init__(self, responder):
        self._responder = responder
        self._buf = b""

    def write(self, data: bytes) -> int:
        cmd = json.loads(data.decode().lstrip(">").strip())
        for line in self._responder(cmd):
            self._buf += line.encode() + b"\n"
        return len(data)

    def flush(self):
        pass

    @property
    def in_waiting(self) -> int:
        return len(self._buf)

    def read(self, n: int = 1) -> bytes:
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def reset_input_buffer(self):
        self._buf = b""

    def close(self):
        type(self).is_open = False


def _envelope(flash, seq, kind="monitor", **decoded):
    return {
        "v": 1,
        "flash": flash,
        "id": f"{flash}-{seq}",
        "station": "stick-abc123",
        "ts": f"t+{1000 + seq}",
        "toolset": "wifi",
        "kind": kind,
        "payload": {"decoded": decoded or {"rate": seq}},
    }


def _responder(cmd):
    op = cmd["op"]
    if op == "hello":
        yield "<" + json.dumps(
            {
                "id": cmd["id"],
                "ok": True,
                "station": {
                    "id": "stick-abc123",
                    "model": "m5stack-sticks3",
                    "fw_version": "test",
                    "toolsets": ["wifi"],
                    "radios": [],
                },
            }
        )
    elif op == "stream_start":
        yield "[beat] noise"
        yield "<" + json.dumps({"id": cmd["id"], "ok": True, "flash": "f00d"})
        yield "!" + json.dumps({"flash": "f00d", "event": "open", "tool": cmd["tool"]})
        for i in range(3):
            yield "!" + json.dumps(_envelope("f00d", i))
    elif op == "stream_stop":
        yield "!" + json.dumps({"flash": "f00d", "event": "close", "count": 3})
        yield "<" + json.dumps({"id": cmd["id"], "ok": True, "count": 3})


def _station():
    st = SerialStation(port="fake", rpc_timeout=2.0)
    st._ser = FakeSerial(_responder)
    return st


async def test_stream_blocking_collects_strikes():
    st = _station()
    res = await st.stream("wifi.monitor", {}, duration_s=0.3)
    assert res["flash"] == "f00d"
    assert len(res["strikes"]) == 3  # control lines filtered out
    assert res["strikes"][0]["kind"] == "monitor"
    assert res["count"] == 3


async def test_stream_dispatch_persists_flash_and_strikes(tmp_path):
    fleet = Fleet()
    st = _station()
    fleet.add(st)
    await st.capability()  # resolves the real station id (as `sferic.stations` would)
    c = Collector(store=Store(tmp_path / "t.db"), fleet=fleet)

    flash_id, strikes = await c.stream_dispatch("wifi.monitor", "stick-abc123", {}, 0.3)
    assert flash_id == "f00d"
    assert len(strikes) == 3
    assert all(s.flash == "f00d" for s in strikes)
    assert strikes[0].meta["device_ts"] == "t+1000"

    stored = c.store.strikes(station="stick-abc123", flash="f00d")
    assert len(stored) == 3
    assert c.store.flashes(station="stick-abc123")[0].strike_count == 3


async def test_stream_dispatch_capability_gate(tmp_path):
    fleet = Fleet()
    st = _station()
    fleet.add(st)
    await st.capability()
    c = Collector(store=Store(tmp_path / "t.db"), fleet=fleet)
    with pytest.raises(CollectorError) as e:
        await c.stream_dispatch("ble.spam", "stick-abc123", {}, 0.3)  # fake only has wifi
    assert e.value.code == "capability_not_present"
