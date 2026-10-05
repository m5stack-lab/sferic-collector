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


async def test_run_clamps_oversized_ms_budget():
    """A huge caller-supplied `ms` must not translate into an unbounded hold
    on the station's single request lock (code-review finding, ADR-0010)."""
    st = _station(lambda cmd: iter(()))
    seen = {}

    def fake_rpc(op, timeout=None, **fields):
        seen["timeout"] = timeout
        return {"ok": True, "result": {}}

    st._rpc = fake_rpc
    await st.run("wifi.scan", {"ms": 10_000_000})
    assert seen["timeout"] == 120000 / 1000.0 + 10.0  # clamped, not ~10000s


async def test_run_rejects_non_numeric_ms():
    def responder(cmd):
        yield "<" + json.dumps({"id": cmd["id"], "ok": True, "result": {}})

    st = _station(responder)
    with pytest.raises(StationToolError) as e:
        await st.run("wifi.scan", {"ms": "soon"})
    assert e.value.code == "bad_params"


async def test_reply_id_mismatch_times_out():
    def responder(cmd):
        yield "<" + json.dumps({"id": cmd["id"] + 99, "ok": True, "station": {}})

    st = _station(responder, rpc_timeout=0.2)  # capability() uses this budget
    with pytest.raises(StationError):
        await st.capability()
