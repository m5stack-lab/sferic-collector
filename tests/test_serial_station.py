"""SerialStation framing tests with a fake serial port — no hardware.

The fake replays a scripted set of lines per written command: log spew, then
the matching `<{...}` reply keyed to the request id.
"""

import json

import pytest

from sferic_collector.serial_station import SerialStation
from sferic_collector.transport import StationError, StationToolError


class FakeSerial:
    is_open = True
    dtr = False

    def __init__(self, responder):
        self._responder = responder
        self._outbox: list[bytes] = []

    def write(self, data: bytes) -> int:
        cmd = json.loads(data.decode().lstrip(">").strip())
        for line in self._responder(cmd):
            self._outbox.append(line.encode() + b"\n")
        return len(data)

    def flush(self):
        pass

    def readline(self) -> bytes:
        return self._outbox.pop(0) if self._outbox else b""

    def reset_input_buffer(self):
        pass

    def close(self):
        type(self).is_open = False


def _station(responder, rpc_timeout: float = 2.0) -> SerialStation:
    st = SerialStation(port="fake", rpc_timeout=rpc_timeout)
    st._ser = FakeSerial(responder)  # skip _open()
    return st


async def test_hello_sets_identity_and_capability():
    def responder(cmd):
        assert cmd["op"] == "hello"
        yield "[beat] up=1s heap=280KB"  # spew before the reply
        yield "<" + json.dumps(
            {
                "id": cmd["id"],
                "ok": True,
                "station": {
                    "id": "stick-abc123",
                    "model": "m5stack-sticks3",
                    "fw_version": "0.1.0 test",
                    "toolsets": ["wifi", "ble", "ir", "sensors"],
                    "radios": ["wifi-2g4", "ble"],
                },
            }
        )

    st = _station(responder)
    cap = await st.capability()
    assert st.id == "stick-abc123"
    assert st.model == "m5stack-sticks3"
    assert cap.toolsets == ["wifi", "ble", "ir", "sensors"]
    assert "wifi-2g4" in cap.radios


async def test_run_ok_returns_result():
    def responder(cmd):
        yield "[tool] noise"
        yield "<" + json.dumps({"id": cmd["id"], "ok": True, "result": {"moving": False}})

    st = _station(responder)
    assert await st.run("sensors.motion", {}) == {"moving": False}


async def test_run_typed_error_raises_stationtoolerror():
    def responder(cmd):
        yield "<" + json.dumps({"id": cmd["id"], "ok": False, "error": "device_only"})

    st = _station(responder)
    with pytest.raises(StationToolError) as e:
        await st.run("wifi.scan", {})
    assert e.value.code == "device_only"


async def test_reply_id_mismatch_times_out():
    def responder(cmd):
        yield "<" + json.dumps({"id": cmd["id"] + 99, "ok": True, "result": {}})

    st = _station(responder, rpc_timeout=0.2)
    with pytest.raises(StationError):
        await st.run("sensors.motion", {})
