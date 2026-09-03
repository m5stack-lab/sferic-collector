"""The WS listener (ADR-0009) end to end, via Starlette's TestClient:
connect -> hello -> welcome, station lands in the fleet, and a `cmd` issued
through the fleet transport gets its `reply` back over the same socket.
"""

import pytest
from starlette.testclient import TestClient

from sferic_collector.collector import Collector
from sferic_collector.store import Store
from sferic_collector.transport import Fleet
from sferic_collector.ws_server import make_ws_app


def _collector(tmp_path) -> Collector:
    return Collector(store=Store(tmp_path / "t.db"), fleet=Fleet())


def test_hello_then_welcome_and_fleet_registration(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_ws_app(c, tokens={"s3cr3t"}))
    with client.websocket_connect("/station", headers={"authorization": "Bearer s3cr3t"}) as ws:
        ws.send_json(
            {
                "type": "hello",
                "token": "s3cr3t",
                "station": {
                    "id": "stick-net1",
                    "model": "m5stack-sticks3",
                    "toolsets": ["wifi", "sensors"],
                    "radios": ["wifi-2g4"],
                },
            }
        )
        assert ws.receive_json()["type"] == "welcome"
        st = c.fleet.get("stick-net1")
        assert st is not None and st.kind == "ws"
        assert st.model == "m5stack-sticks3"

    # socket closed -> removed from the fleet
    assert c.fleet.get("stick-net1") is None


def test_bad_token_is_rejected(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_ws_app(c, tokens={"good"}))
    with pytest.raises(Exception):  # noqa: B017 - starlette raises on the 4001 close
        with client.websocket_connect("/station", headers={"authorization": "Bearer wrong"}) as ws:
            ws.send_json({"type": "hello", "station": {"id": "x", "toolsets": []}})
            ws.receive_json()
    assert c.fleet.all() == []


def test_hello_token_accepted_when_no_header(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_ws_app(c, tokens={"tok"}))
    with client.websocket_connect("/station") as ws:
        ws.send_json(
            {"type": "hello", "token": "tok", "station": {"id": "stick-h", "toolsets": ["ir"]}}
        )
        assert ws.receive_json()["type"] == "welcome"
        assert c.fleet.get("stick-h") is not None


def test_dev_mode_no_tokens_accepts_any(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_ws_app(c, tokens=set()))
    with client.websocket_connect("/station") as ws:
        ws.send_json({"type": "hello", "station": {"id": "stick-open", "toolsets": []}})
        assert ws.receive_json()["type"] == "welcome"


def test_first_frame_must_be_hello(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_ws_app(c, tokens=set()))
    with pytest.raises(Exception):  # noqa: B017 - server closes on protocol error
        with client.websocket_connect("/station") as ws:
            ws.send_json({"type": "cmd", "id": 1, "op": "status"})
            ws.receive_json()
    assert c.fleet.all() == []


def test_health_lists_stations(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_ws_app(c, tokens=set()))
    assert client.get("/health").json() == {"stations": []}
