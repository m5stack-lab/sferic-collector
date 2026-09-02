"""Emit two-step path: SerialStation.emit_arm/emit_fire + Collector.emit_dispatch,
FakeSerial-backed, no hardware and nothing actually emitted."""

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
        for line in self._responder(json.loads(data.decode().lstrip(">").strip())):
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


_TOKEN = "d5f8a5"


def _responder(cmd):
    op = cmd["op"]
    if op == "hello":
        yield "<" + json.dumps(
            {
                "id": cmd["id"],
                "ok": True,
                "station": {
                    "id": "stick-abc123",
                    "toolsets": ["ir", "ble"],
                    "radios": [],
                },
            }
        )
    elif op == "emit_arm":
        if cmd["tool"] == "ir.tvbgone":
            yield "<" + json.dumps(
                {"id": cmd["id"], "ok": True, "token": _TOKEN, "expires_ms": 60000}
            )
        else:
            yield "<" + json.dumps({"id": cmd["id"], "ok": False, "error": "busy"})
    elif op == "emit_fire":
        if cmd["token"] == _TOKEN:
            yield "<" + json.dumps({"id": cmd["id"], "ok": True, "result": {"sent": 39}})
        else:
            yield "<" + json.dumps({"id": cmd["id"], "ok": False, "error": "bad_token"})


def _station():
    st = SerialStation(port="fake", rpc_timeout=2.0)
    st._ser = FakeSerial(_responder)
    return st


@pytest.fixture
async def collector(tmp_path):
    fleet = Fleet()
    st = _station()
    fleet.add(st)
    await st.capability()
    return Collector(store=Store(tmp_path / "t.db"), fleet=fleet)


async def test_arm_returns_token_no_strike(collector):
    r = await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, None)
    assert r["armed"] is True
    assert r["confirm_token"] == _TOKEN
    assert collector.store.strikes() == []  # nothing fired, nothing stored


async def test_fire_with_token_stores_strike(collector):
    await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, None)  # arm
    r = await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, _TOKEN)
    assert r["fired"] is True
    assert r["result"] == {"sent": 39}
    assert collector.store.strikes()[0].kind == "emit"


async def test_fire_bad_token_errors(collector):
    await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, None)  # arm
    with pytest.raises(CollectorError) as e:
        await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, "nope")
    assert e.value.code == "bad_token"


async def test_fire_without_arm_rejected_before_firmware(collector):
    with pytest.raises(CollectorError) as e:  # no prior arm on this (station, tool)
        await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, _TOKEN)
    assert e.value.code == "bad_token"


async def test_token_bound_to_tool(collector):
    await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, None)  # arm tvbgone
    with pytest.raises(CollectorError) as e:  # ...but try to fire ir.replay with it
        await collector.emit_dispatch("ir.replay", "stick-abc123", None, _TOKEN)
    assert e.value.code == "bad_token"


async def test_disabled_emit_tool_rejected(collector):
    collector.disabled.add(("stick-abc123", "ir.tvbgone"))
    with pytest.raises(CollectorError) as e:
        await collector.emit_dispatch("ir.tvbgone", "stick-abc123", None, None)
    assert e.value.code == "capability_disabled"


async def test_arm_busy_passes_through(collector):
    with pytest.raises(CollectorError) as e:
        await collector.emit_dispatch("ble.hid", "stick-abc123", {"text": "x"}, None)
    assert e.value.code == "busy"


async def test_emit_capability_gate(collector):
    with pytest.raises(CollectorError) as e:
        await collector.emit_dispatch("usb.hid", "stick-abc123", {"text": "x"}, None)
    assert e.value.code == "capability_not_present"  # fake declares only ir, ble
